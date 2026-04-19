"""
Trend analysis logic for Naver health food keywords.
Identifies growing trends, early-mover opportunities, and market phases.
"""

import numpy as np
from typing import Optional


class TrendAnalyzer:
    """Analyzes keyword trend data to identify growth opportunities."""

    def __init__(self):
        self.min_data_points = 3  # Minimum data points required for analysis

    def analyze_keywords(self, keyword_trends: dict) -> list[dict]:
        """
        Analyze trend data for all keywords and score them for market opportunity.

        Args:
            keyword_trends: {
                "longterm": {kw: [{"period", "ratio"}]},
                "shortterm_1yr": {kw: [...]},
                "shortterm_3mo": {kw: [...]},
                "shortterm_1mo": {kw: [...]},
            }

        Returns:
            List of analyzed keyword dicts, sorted by opportunity score (descending).
            Each dict contains:
                - keyword: str
                - recent_growth_rate: float (% change last 3mo vs prev 3mo)
                - longterm_trend: "rising" | "stable" | "falling" | "new"
                - early_mover_score: 0-100
                - consistency_score: 0-100
                - peak_ratio: float (current vs all-time peak, 0-1)
                - trend_phase: "early_rising" | "growing" | "peak" | "declining" | "stable" | "unknown"
                - opportunity_score: 0-100
                - data_quality: "good" | "limited" | "no_data"
                - avg_ratio_3mo: float
                - avg_ratio_1yr: float
                - max_ratio_alltime: float
                - current_ratio: float
        """
        keywords = set()
        for k in keyword_trends:
            keywords.update(keyword_trends[k].keys())

        print(f"[분석기] {len(keywords)}개 키워드 분석 시작...")

        analyzed = []
        for kw in keywords:
            try:
                result = self._analyze_single_keyword(kw, keyword_trends)
                analyzed.append(result)
            except Exception as e:
                print(f"[분석기] '{kw}' 분석 오류: {e}")
                analyzed.append(self._empty_analysis(kw))

        # Sort by opportunity score (highest first)
        analyzed.sort(key=lambda x: x["opportunity_score"], reverse=True)

        print(f"[분석기] 분석 완료. 상위 기회 키워드:")
        for item in analyzed[:5]:
            print(f"  - {item['keyword']}: 기회점수={item['opportunity_score']:.1f}, "
                  f"단계={item['trend_phase']}, 성장률={item['recent_growth_rate']:.1f}%")

        return analyzed

    def _analyze_single_keyword(self, keyword: str, keyword_trends: dict) -> dict:
        """Perform full analysis for a single keyword."""
        # Extract data series
        lt_data = keyword_trends.get("longterm", {}).get(keyword, [])
        yr_data = keyword_trends.get("shortterm_1yr", {}).get(keyword, [])
        mo3_data = keyword_trends.get("shortterm_3mo", {}).get(keyword, [])
        mo1_data = keyword_trends.get("shortterm_1mo", {}).get(keyword, [])

        # Extract ratio values as numpy arrays
        lt_ratios = np.array([d["ratio"] for d in lt_data]) if lt_data else np.array([])
        yr_ratios = np.array([d["ratio"] for d in yr_data]) if yr_data else np.array([])
        mo3_ratios = np.array([d["ratio"] for d in mo3_data]) if mo3_data else np.array([])
        mo1_ratios = np.array([d["ratio"] for d in mo1_data]) if mo1_data else np.array([])

        # Data quality check
        has_longterm = len(lt_ratios) >= self.min_data_points
        has_shortterm = len(yr_ratios) >= self.min_data_points

        if not has_longterm and not has_shortterm:
            return self._empty_analysis(keyword)

        data_quality = "good" if (has_longterm and has_shortterm) else "limited"

        # Use the best available data for calculations
        primary_data = lt_ratios if has_longterm else yr_ratios

        # --- Current ratio ---
        current_ratio = float(mo1_ratios[-1]) if len(mo1_ratios) > 0 else (
            float(yr_ratios[-1]) if len(yr_ratios) > 0 else (
                float(lt_ratios[-1]) if len(lt_ratios) > 0 else 0.0
            )
        )

        # --- All-time peak ---
        all_ratios = np.concatenate([lt_ratios, yr_ratios, mo3_ratios, mo1_ratios])
        all_ratios = all_ratios[~np.isnan(all_ratios)]
        max_ratio_alltime = float(np.max(all_ratios)) if len(all_ratios) > 0 else 0.0

        # --- Average ratios ---
        avg_ratio_3mo = float(np.mean(mo3_ratios)) if len(mo3_ratios) > 0 else (
            float(np.mean(yr_ratios[-12:])) if len(yr_ratios) >= 12 else (
                float(np.mean(yr_ratios)) if len(yr_ratios) > 0 else 0.0
            )
        )

        avg_ratio_1yr = float(np.mean(yr_ratios)) if len(yr_ratios) > 0 else (
            float(np.mean(lt_ratios[-12:])) if len(lt_ratios) >= 12 else (
                float(np.mean(lt_ratios)) if len(lt_ratios) > 0 else 0.0
            )
        )

        # --- Recent growth rate (last 3mo vs prev 3mo) ---
        recent_growth_rate = self._calc_growth_rate(yr_ratios, mo3_ratios)

        # --- Long-term trend ---
        longterm_trend = self._calc_longterm_trend(lt_ratios, yr_ratios)

        # --- Peak ratio ---
        peak_ratio = (current_ratio / max_ratio_alltime) if max_ratio_alltime > 0 else 0.0

        # --- Consistency score ---
        consistency_score = self._calc_consistency_score(lt_ratios, yr_ratios)

        # --- Early mover score ---
        early_mover_score = self._calc_early_mover_score(
            lt_ratios, yr_ratios, mo3_ratios, recent_growth_rate, peak_ratio
        )

        # --- Trend phase ---
        trend_phase = self._determine_trend_phase(
            lt_ratios, yr_ratios, mo3_ratios, recent_growth_rate,
            peak_ratio, longterm_trend, early_mover_score
        )

        # --- Steady growth score ---
        steady_growth_score = self._calc_steady_growth_score(
            lt_ratios, yr_ratios, mo3_ratios,
            consistency_score, longterm_trend, recent_growth_rate
        )

        # --- Opportunity score (weighted composite) ---
        opportunity_score = self._calc_opportunity_score(
            early_mover_score=early_mover_score,
            recent_growth_rate=recent_growth_rate,
            consistency_score=consistency_score,
            peak_ratio=peak_ratio,
            longterm_trend=longterm_trend,
            trend_phase=trend_phase,
            avg_ratio_3mo=avg_ratio_3mo,
            steady_growth_score=steady_growth_score,
            lt_ratios=lt_ratios,
            yr_ratios=yr_ratios,
            mo3_ratios=mo3_ratios,
        )

        return {
            "keyword": keyword,
            "recent_growth_rate": recent_growth_rate,
            "longterm_trend": longterm_trend,
            "early_mover_score": early_mover_score,
            "consistency_score": consistency_score,
            "steady_growth_score": steady_growth_score,
            "peak_ratio": peak_ratio,
            "trend_phase": trend_phase,
            "opportunity_score": opportunity_score,
            "data_quality": data_quality,
            "avg_ratio_3mo": avg_ratio_3mo,
            "avg_ratio_1yr": avg_ratio_1yr,
            "max_ratio_alltime": max_ratio_alltime,
            "current_ratio": current_ratio,
        }

    def _calc_growth_rate(self, yr_ratios: np.ndarray, mo3_ratios: np.ndarray) -> float:
        """
        Calculate recent growth rate: last 3 months vs previous 3 months.
        Uses weekly data if available, falls back to monthly.
        """
        # Use 3-month weekly data if available
        if len(mo3_ratios) >= 4:
            mid = len(mo3_ratios) // 2
            recent_avg = float(np.mean(mo3_ratios[mid:]))
            prev_avg = float(np.mean(mo3_ratios[:mid]))
        elif len(yr_ratios) >= 8:
            # Use 1yr weekly data: last 12 weeks vs previous 12 weeks
            n = min(12, len(yr_ratios) // 2)
            recent_avg = float(np.mean(yr_ratios[-n:]))
            prev_avg = float(np.mean(yr_ratios[-n*2:-n]))
        else:
            return 0.0

        if prev_avg <= 0:
            return 100.0 if recent_avg > 0 else 0.0

        return ((recent_avg - prev_avg) / prev_avg) * 100

    def _calc_longterm_trend(self, lt_ratios: np.ndarray, yr_ratios: np.ndarray) -> str:
        """
        Determine long-term trend direction.
        Returns: "rising" | "stable" | "falling" | "new"
        """
        data = lt_ratios if len(lt_ratios) >= 12 else yr_ratios
        if len(data) < self.min_data_points:
            return "new"

        # Check if keyword is "new" (no historical data before recent period)
        if len(lt_ratios) >= 12:
            early_avg = float(np.mean(lt_ratios[:12]))
            if early_avg < 1.0:
                return "new"

        # Linear regression slope
        x = np.arange(len(data))
        if len(x) < 2:
            return "stable"

        slope = np.polyfit(x, data, 1)[0]

        # Normalize slope relative to mean value
        mean_val = float(np.mean(data))
        if mean_val <= 0:
            return "stable"

        normalized_slope = slope / mean_val

        if normalized_slope > 0.01:
            return "rising"
        elif normalized_slope < -0.01:
            return "falling"
        else:
            return "stable"

    def _calc_consistency_score(self, lt_ratios: np.ndarray, yr_ratios: np.ndarray) -> float:
        """
        Calculate consistency score: how steadily the keyword has been growing.
        Score 0-100, higher = more consistent upward trend.
        """
        data = lt_ratios if len(lt_ratios) >= 12 else yr_ratios
        if len(data) < 4:
            return 50.0

        # Method: count how many consecutive windows show growth
        window = max(2, len(data) // 6)
        windows = []
        for i in range(0, len(data) - window, window):
            windows.append(float(np.mean(data[i:i + window])))

        if len(windows) < 2:
            return 50.0

        positive_transitions = sum(
            1 for i in range(1, len(windows))
            if windows[i] > windows[i - 1]
        )
        total_transitions = len(windows) - 1

        # Also check overall direction consistency using rolling correlation
        x = np.arange(len(data))
        try:
            corr = float(np.corrcoef(x, data)[0, 1])
        except Exception:
            corr = 0.0

        # Combined score
        proportion_score = (positive_transitions / total_transitions) * 50
        correlation_score = max(0, corr) * 50

        return min(100.0, proportion_score + correlation_score)

    def _is_post_peak_decline(
        self,
        lt_ratios: np.ndarray,
        yr_ratios: np.ndarray,
        mo3_ratios: np.ndarray,
        peak_ratio: float,
    ) -> bool:
        """
        Detect "peaked and now declining" pattern.
        세 가지 케이스를 감지합니다:
        1. 단기 고점 후 하락: 1년 주별 데이터 내에서 고점 후 하락
        2. 장기 고점 후 하락: 장기 데이터에서 최근 24개월 이내 고점 후 현재 크게 하락
        3. 오래된 이슈 키워드: 언제 고점이었든 고점 대비 현재 25% 이하이고 절대값도 낮음
           예) 몇 년 전 이슈됐다가 지금은 거의 검색 안 되는 키워드
        """
        if peak_ratio > 0.85:
            return False  # Still near peak, not post-peak

        # --- Case 1: 1년 주별 데이터 내 단기 고점 후 하락 ---
        if len(yr_ratios) >= 12:
            all_time_max_yr = float(np.max(yr_ratios))
            if all_time_max_yr > 0:
                peak_idx = int(np.argmax(yr_ratios))
                recent_threshold = int(len(yr_ratios) * 0.65)
                if peak_idx >= recent_threshold:
                    current = float(yr_ratios[-1])
                    if current < all_time_max_yr * 0.75:
                        return True

        # --- Case 2: 장기(월별) 데이터에서 최근 24개월 이내 고점 후 장기 하락 ---
        if len(lt_ratios) >= 24:
            all_time_max_lt = float(np.max(lt_ratios))
            if all_time_max_lt <= 0:
                return False

            peak_idx_lt = int(np.argmax(lt_ratios))
            if peak_idx_lt >= len(lt_ratios) - 24:
                current_lt = float(lt_ratios[-1])
                recent_avg_lt = float(np.mean(lt_ratios[-6:])) if len(lt_ratios) >= 6 else current_lt
                if recent_avg_lt < all_time_max_lt * 0.60:
                    return True

        # --- Case 3: 오래된 이슈 키워드 (고점 시기 무관) ---
        # 한때 의미 있는 검색량(고점 20 이상)이 있었으나
        # 현재는 고점 대비 25% 이하로 떨어지고 최근 평균도 낮은 경우
        if len(lt_ratios) >= 12:
            all_time_max_lt = float(np.max(lt_ratios))
            if all_time_max_lt >= 20:
                recent_avg = float(np.mean(lt_ratios[-6:])) if len(lt_ratios) >= 6 else float(lt_ratios[-1])
                if recent_avg < all_time_max_lt * 0.25 and recent_avg < 10:
                    return True

        return False

    def _calc_steady_growth_score(
        self,
        lt_ratios: np.ndarray,
        yr_ratios: np.ndarray,
        mo3_ratios: np.ndarray,
        consistency_score: float,
        longterm_trend: str,
        recent_growth_rate: float,
    ) -> float:
        """
        꾸준한 우상향 점수 (0-100).
        선점 기회와 별도로, 검증된 장기 성장 키워드를 평가합니다.
        """
        score = 0.0

        # 장기 트렌드가 상승 중이어야 함
        if longterm_trend not in ("rising", "new"):
            return 0.0

        # 일관성 점수가 높을수록 (꾸준히 오른다는 의미)
        score += consistency_score * 0.50

        # 장기 데이터에서 연속 성장 기간 체크
        if len(lt_ratios) >= 12:
            # 최근 12개월을 4분기로 나누어 연속 상승 확인
            q = len(lt_ratios) // 4
            quarters = [float(np.mean(lt_ratios[i*q:(i+1)*q])) for i in range(4)]
            rising_quarters = sum(1 for i in range(1, 4) if quarters[i] > quarters[i-1])
            score += (rising_quarters / 3) * 30.0

        # 최근 3개월도 성장 중이면 추가 점수
        if recent_growth_rate > 5:
            score += 20.0
        elif recent_growth_rate > 0:
            score += 10.0

        return min(100.0, max(0.0, score))

    def _calc_early_mover_score(
        self,
        lt_ratios: np.ndarray,
        yr_ratios: np.ndarray,
        mo3_ratios: np.ndarray,
        recent_growth_rate: float,
        peak_ratio: float,
    ) -> float:
        """
        Calculate early mover score: high if the keyword is recently rising
        from a previously low base (early-stage trend).
        Score: 0-100, higher = better early mover opportunity.
        """
        score = 0.0

        # Disqualify post-peak decline: penalize heavily
        if self._is_post_peak_decline(lt_ratios, yr_ratios, mo3_ratios, peak_ratio):
            return max(0.0, score - 30.0)

        # Component 1: Recent growth rate (strong positive growth)
        # Scaled: 100% growth → 40 points; negative growth gives 0
        growth_score = min(40.0, max(0.0, recent_growth_rate / 2.5))
        score += growth_score

        # Component 2: Low historical base (keyword wasn't popular before)
        if len(lt_ratios) >= 24:
            historical_avg = float(np.mean(lt_ratios[:-12]))  # everything before last year
            recent_avg = float(np.mean(lt_ratios[-12:])) if len(lt_ratios) >= 12 else 0

            if historical_avg < 10:
                # Very low historical base - strong early mover signal
                score += 25.0
                if recent_avg > historical_avg * 2:
                    score += 15.0  # Recent surge from low base
            elif historical_avg < 30:
                score += 10.0

        elif len(yr_ratios) >= 4:
            # For newer data, look at how much the keyword has grown within the year
            early_yr = float(np.mean(yr_ratios[:len(yr_ratios)//3]))
            late_yr = float(np.mean(yr_ratios[-len(yr_ratios)//3:]))
            if early_yr > 0 and late_yr > early_yr * 1.5:
                score += 20.0

        # Component 3: Not at peak yet (room to grow)
        # Only counts as opportunity if current trend is also positive (not declining from peak)
        if peak_ratio < 0.5 and recent_growth_rate > 0:
            score += 15.0  # Low ratio + still growing = genuine early stage
        elif peak_ratio < 0.75 and recent_growth_rate > 0:
            score += 8.0

        # Component 4: Acceleration (3mo trend steeper than 1yr trend)
        if len(mo3_ratios) >= 4 and len(yr_ratios) >= 12:
            mo3_slope = float(np.polyfit(np.arange(len(mo3_ratios)), mo3_ratios, 1)[0])
            yr_slope = float(np.polyfit(np.arange(len(yr_ratios)), yr_ratios, 1)[0])
            if mo3_slope > yr_slope > 0:
                score += 10.0  # Accelerating trend
            elif mo3_slope > 0 and yr_slope <= 0:
                score += 15.0  # Reversal/inflection point

        return min(100.0, max(0.0, score))

    def _determine_trend_phase(
        self,
        lt_ratios: np.ndarray,
        yr_ratios: np.ndarray,
        mo3_ratios: np.ndarray,
        recent_growth_rate: float,
        peak_ratio: float,
        longterm_trend: str,
        early_mover_score: float,
    ) -> str:
        """
        Determine the current trend phase.
        Returns: "early_rising" | "growing" | "peak" | "declining" | "stable" | "unknown"
        """
        if len(lt_ratios) < self.min_data_points and len(yr_ratios) < self.min_data_points:
            return "unknown"

        # Post-peak decline: peaked recently and now falling
        if self._is_post_peak_decline(lt_ratios, yr_ratios, mo3_ratios, peak_ratio):
            return "declining"

        # Check for decline (lowered threshold from -20 to -10)
        if recent_growth_rate < -10:
            return "declining"

        # Check for peak (currently at or near all-time high)
        if peak_ratio > 0.9 and longterm_trend != "new":
            if recent_growth_rate < 5:
                return "peak"

        # Check for early rising (high early mover score + positive recent growth)
        if early_mover_score >= 55 and recent_growth_rate > 15:
            return "early_rising"

        # Check for growing (steady positive trend)
        if recent_growth_rate > 10 or longterm_trend == "rising":
            if peak_ratio < 0.8:
                return "growing"

        # Stable
        if abs(recent_growth_rate) <= 15 and longterm_trend == "stable":
            return "stable"

        # Declining
        if longterm_trend == "falling" and recent_growth_rate < 0:
            return "declining"

        # Default: growing if positive, stable otherwise
        if recent_growth_rate > 5:
            return "growing"

        return "stable"

    def _calc_opportunity_score(
        self,
        early_mover_score: float,
        recent_growth_rate: float,
        consistency_score: float,
        peak_ratio: float,
        longterm_trend: str,
        trend_phase: str,
        avg_ratio_3mo: float,
        steady_growth_score: float = 0.0,
        lt_ratios: np.ndarray = None,
        yr_ratios: np.ndarray = None,
        mo3_ratios: np.ndarray = None,
    ) -> float:
        """
        Calculate final opportunity score (0-100).
        선점 기회 + 안정 성장 두 가지 경로로 높은 점수 가능.
        """
        score = 0.0

        is_post_peak = (
            lt_ratios is not None and
            self._is_post_peak_decline(
                lt_ratios,
                yr_ratios if yr_ratios is not None else np.array([]),
                mo3_ratios if mo3_ratios is not None else np.array([]),
                peak_ratio,
            )
        )

        # 죽은 이슈 키워드면 여기서 조기 종료
        if is_post_peak and trend_phase == "declining":
            return max(0.0, 10.0 - (1.0 - peak_ratio) * 20)

        # Weight 1: Early mover score (선점 가중치)
        score += early_mover_score * 0.35

        # Weight 2: Steady growth score (안정 성장 가중치) — 두 경로 모두 반영
        score += steady_growth_score * 0.20

        # Weight 3: Recent growth rate
        growth_normalized = min(100.0, max(0.0, (recent_growth_rate + 50) / 2.5))
        score += growth_normalized * 0.20

        # Weight 4: Consistency score
        score += consistency_score * 0.15

        # Weight 5: Room to grow (성장 중일 때만)
        if not is_post_peak and recent_growth_rate > 0:
            room_to_grow = (1.0 - peak_ratio) * 100
            score += room_to_grow * 0.10

        # Long-term trend bonus
        trend_bonus = {
            "new": 15.0,
            "rising": 12.0,   # 기존 10 → 12 (꾸준한 성장 더 높게 평가)
            "stable": 0.0,
            "falling": -20.0,
        }
        score += trend_bonus.get(longterm_trend, 0.0)

        # Phase bonus
        phase_bonus = {
            "early_rising": 15.0,
            "growing": 10.0,   # 기존 8 → 10
            "stable": 0.0,
            "peak": -5.0,
            "declining": -25.0,
            "unknown": -5.0,
        }
        score += phase_bonus.get(trend_phase, 0.0)

        # 꾸준한 우상향 보너스: 일관성 높고 장기 상승이면 추가
        if consistency_score >= 70 and longterm_trend == "rising":
            score += 8.0

        if is_post_peak:
            score -= 15.0

        # 시장 규모 페널티: 새로 뜨는 키워드(new)는 제외
        # - longterm_trend == "new": 원래부터 검색량 없었던 신규 키워드 → 선점 기회일 수 있음, 패널티 없음
        # - 기존에 항상 작았던 키워드만 페널티 (수요 자체가 없는 시장)
        if longterm_trend != "new":
            if avg_ratio_3mo < 1.0:
                score -= 10.0
            elif avg_ratio_3mo < 3.0:
                score -= 5.0

        return min(100.0, max(0.0, score))

    def _empty_analysis(self, keyword: str) -> dict:
        """Return an empty analysis dict for keywords with no data."""
        return {
            "keyword": keyword,
            "recent_growth_rate": 0.0,
            "longterm_trend": "unknown",
            "early_mover_score": 0.0,
            "consistency_score": 0.0,
            "steady_growth_score": 0.0,
            "peak_ratio": 0.0,
            "trend_phase": "unknown",
            "opportunity_score": 0.0,
            "data_quality": "no_data",
            "avg_ratio_3mo": 0.0,
            "avg_ratio_1yr": 0.0,
            "max_ratio_alltime": 0.0,
            "current_ratio": 0.0,
        }

    def get_summary_stats(self, analyzed: list[dict]) -> dict:
        """Generate summary statistics across all analyzed keywords."""
        if not analyzed:
            return {}

        phases = [a["trend_phase"] for a in analyzed]
        phase_counts = {p: phases.count(p) for p in set(phases)}

        early_rising = [a for a in analyzed if a["trend_phase"] == "early_rising"]
        growing = [a for a in analyzed if a["trend_phase"] == "growing"]
        declining = [a for a in analyzed if a["trend_phase"] == "declining"]

        avg_growth = np.mean([a["recent_growth_rate"] for a in analyzed])

        return {
            "total_keywords": len(analyzed),
            "phase_distribution": phase_counts,
            "early_rising_count": len(early_rising),
            "growing_count": len(growing),
            "declining_count": len(declining),
            "avg_recent_growth_rate": float(avg_growth),
            "top_opportunity": analyzed[0]["keyword"] if analyzed else None,
            "top_early_mover": max(analyzed, key=lambda x: x["early_mover_score"])["keyword"] if analyzed else None,
        }


if __name__ == "__main__":
    # Test with mock data
    import json
    from datetime import datetime, timedelta

    def make_trend(base=10, growth=0.5, length=48, noise=2):
        """Generate mock trend data."""
        data = []
        for i in range(length):
            ratio = base + growth * i + np.random.normal(0, noise)
            ratio = max(0, ratio)
            date = (datetime(2020, 1, 1) + timedelta(weeks=i)).strftime("%Y-%m-%d")
            data.append({"period": date, "ratio": ratio})
        return data

    mock_trends = {
        "longterm": {
            "홍삼": make_trend(50, 0.1, 96),
            "새싹보리": make_trend(5, 0.8, 96),
            "콜라겐": make_trend(30, 0.3, 96),
            "아쉬와간다": make_trend(1, 1.2, 48),
        },
        "shortterm_1yr": {
            "홍삼": make_trend(55, 0.05, 52),
            "새싹보리": make_trend(40, 1.5, 52),
            "콜라겐": make_trend(45, 0.1, 52),
            "아쉬와간다": make_trend(10, 2.0, 52),
        },
        "shortterm_3mo": {
            "홍삼": make_trend(56, 0.0, 13),
            "새싹보리": make_trend(60, 2.0, 13),
            "콜라겐": make_trend(47, 0.2, 13),
            "아쉬와간다": make_trend(30, 3.0, 13),
        },
        "shortterm_1mo": {
            "홍삼": make_trend(56, 0.0, 30),
            "새싹보리": make_trend(80, 1.0, 30),
            "콜라겐": make_trend(48, 0.1, 30),
            "아쉬와간다": make_trend(60, 2.0, 30),
        },
    }

    analyzer = TrendAnalyzer()
    results = analyzer.analyze_keywords(mock_trends)

    print("\n=== 분석 결과 ===")
    for r in results:
        print(f"\n{r['keyword']}:")
        print(f"  트렌드 단계: {r['trend_phase']}")
        print(f"  기회 점수: {r['opportunity_score']:.1f}")
        print(f"  얼리무버 점수: {r['early_mover_score']:.1f}")
        print(f"  최근 성장률: {r['recent_growth_rate']:.1f}%")
        print(f"  장기 트렌드: {r['longterm_trend']}")
