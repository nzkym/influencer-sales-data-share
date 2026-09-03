"""매일 자동실행 오케스트레이션.

2026-07-22: run_daily.bat(cmd.exe 배치파일)로 돌리던 걸 파이썬으로 완전히 대체함.
한글이 포함된 공백 경로를 배치파일 안에서 다루면(cd /d, set 변수 등) 실행 환경에 따라
간헐적으로 파싱이 깨지는 문제가 하루종일 여러 형태로 재현됐다(상위 폴더로 잘못 이동,
echo 문구 파싱 실패, set 변수값 중간에서 잘림 등) - 원인을 매번 다르게 고쳐도 계속
재발해서, 아예 cmd.exe 배치 파싱 자체를 거치지 않도록 파이썬으로 옮겼다. 작업
스케줄러는 이제 이 파일을 직접 실행한다(`python.exe run_daily.py`), .bat 경유 없음.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
PYTHON = sys.executable
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

STEPS = [
    ["run_draft.py"],
    ["gpt_reviewer.py"],
    ["poster.py", "--live"],
]


def main():
    # 예전엔 각 단계의 stdout/stderr를 아예 캡처 안 하고 그냥 subprocess.run()만 불렀다
    # (2026-08-14 발견: gpt_reviewer.py가 스케줄러 실행에서만 조용히 죽어서 하루치
    # 초안 63건이 전부 승인 안 된 채 방치됐는데, 왜 죽었는지 실제 에러 메시지가
    # 어디에도 안 남아서 원인 파악이 안 됐음 - pythonw.exe로 실행되는 무인 환경이라
    # stderr가 콘솔 없이 그냥 버려짐). 이제 실패한 단계는 stdout+stderr 전체를
    # naver_login.log와 같은 폴더에 남겨서, 다음에 같은 일이 생기면 바로 원인을 볼 수
    # 있게 한다.
    failed_steps = []
    for step in STEPS:
        script = BASE_DIR / step[0]
        print(f"step: {' '.join(step)}")
        result = subprocess.run(
            [PYTHON, str(script), *step[1:]], cwd=BASE_DIR,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # 2026-09-03: 이 단계 실패 중에 자동수정(self_heal)이 원인을 찾아 코드를
            # 고쳐서 커밋했을 수 있다(collector/naver_login 등에서 report_failure ->
            # self_heal.trigger가 동기적으로 실행됨). 문제는 그 수정이 "디스크의 코드"만
            # 바꿀 뿐, 이미 떠 있는 이 프로세스가 예전에 import해둔 모듈은 그대로라
            # 같은 프로세스 안에서는 재시도해도 소용없다는 것 - 실제로 2026-09-02 밤
              # collect_NUTONE에서 자동수정이 고쳤는데도 바로 이어진 collect_JDHEALTH가
            # 같은 오류로 또 실패했었다(사람이 다음날 수동으로 새 프로세스를 띄워서야
            # 해결됨). 그래서 실패한 단계를 "새 프로세스"로 1회 재시도한다 - 새
            # 인터프리터는 방금 고쳐진 코드를 디스크에서 새로 읽으므로, 자동수정이
            # 실제로 원인을 고쳤다면 이 재시도가 그날 밤 안에 바로 성공해서 사람이
            # 다음날 수동으로 다시 돌릴 필요가 없어진다. 자동수정이 없었거나 원인을
            # 못 고쳤으면 재시도도 똑같이 실패하고 아래 기존 로직(실패 기록+알림)으로 간다.
            print(f"  -> 1차 실패(returncode={result.returncode}), 자동수정 반영 여부 확인차 새 프로세스로 재시도")
            retry_result = subprocess.run(
                [PYTHON, str(script), *step[1:]], cwd=BASE_DIR,
                capture_output=True, text=True,
            )
            if retry_result.returncode == 0:
                print(f"  -> 재시도 성공(자동수정으로 복구된 것으로 보임)")
                if retry_result.stdout:
                    print(retry_result.stdout, end="")
                continue
            result = retry_result

        if result.returncode != 0:
            failed_steps.append(step[0])
            crash_log = LOG_DIR / "step_failures.log"
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n{datetime.now().isoformat()} step={' '.join(step)} returncode={result.returncode} (재시도 포함 2회 모두 실패)\n")
                f.write(f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n")
            print(f"  -> 재시도도 실패(returncode={result.returncode}), 상세는 {crash_log} 참고")
        else:
            # 실패 안 했어도 stdout은 그대로 콘솔에 보여준다(기존처럼 스케줄러 로그에서 볼 수 있게).
            if result.stdout:
                print(result.stdout, end="")

    subprocess.run(
        [PYTHON, str(BASE_DIR / "daily_run_alert.py"), "1" if failed_steps else "0", ",".join(failed_steps)],
        cwd=BASE_DIR,
    )


if __name__ == "__main__":
    main()
