"""The remaining-time estimate prices files as well as bytes.

Measured against a real run (2026-08-12, 199 files / 287.53 GB): at 14m 11s of
copying, 171.16 GB was done across 43 files. The old pure-byte model reported
9m 38s left; the run actually needed 11m 27s. It was optimistic because the 43
completed files averaged ~4 GB while the 156 remaining averaged ~0.75 GB, many
of them artwork and NFOs whose cost is per-file rather than per-byte.
"""

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('fcntl', MagicMock())
for _mod_name in ['plexapi', 'plexapi.server', 'plexapi.video', 'plexapi.myplex',
                  'plexapi.exceptions', 'requests', 'apscheduler',
                  'apscheduler.schedulers', 'apscheduler.schedulers.background',
                  'apscheduler.triggers', 'apscheduler.triggers.cron',
                  'apscheduler.triggers.interval']:
    sys.modules.setdefault(_mod_name, MagicMock())

GB = 1024 ** 3


def _estimate(**kw):
    from web.services.operation_runner import OperationRunner
    return OperationRunner._estimate_remaining_seconds(**kw)


class TestTheRealRun:
    """Numbers taken from the 08:30 run on 2026-08-12."""

    ELAPSED = 14 * 60 + 11        # copying began 08:50:09, sampled 09:04:20
    DONE = int(171.16 * GB)
    REMAINING = int((287.53 - 171.16) * GB)
    FILES_DONE = 43
    FILES_LEFT = 156
    ACTUAL_LEFT = 11 * 60 + 27    # finished 09:15:47

    def test_pure_byte_rate_is_the_optimistic_baseline(self):
        """What the old model produced, kept as the thing to beat."""
        rate = self.DONE / self.ELAPSED
        naive = self.REMAINING / rate

        assert 560 < naive < 600, naive          # ~9m 38s
        assert naive < self.ACTUAL_LEFT * 0.9    # meaningfully under

    def test_per_file_term_closes_most_of_the_gap(self):
        # A sustained rate slightly above the batch average, as a peak sample
        # taken during large-file transfer would be.
        peak = (self.DONE / self.ELAPSED) * 1.08

        eta = _estimate(elapsed=self.ELAPSED, bytes_done=self.DONE,
                        bytes_remaining=self.REMAINING, files_done=self.FILES_DONE,
                        files_remaining=self.FILES_LEFT, peak_rate=peak)

        naive = self.REMAINING / (self.DONE / self.ELAPSED)
        assert eta > naive, "must be less optimistic than the pure byte model"
        assert abs(eta - self.ACTUAL_LEFT) < abs(naive - self.ACTUAL_LEFT), (
            f"estimate {eta:.0f}s should beat naive {naive:.0f}s "
            f"against actual {self.ACTUAL_LEFT}s"
        )


class TestBehaviour:

    def test_a_tail_of_tiny_files_is_not_free(self):
        """500 sidecars totalling almost nothing still take time."""
        eta = _estimate(elapsed=600, bytes_done=100 * GB, bytes_remaining=1024,
                        files_done=20, files_remaining=500, peak_rate=200 * 1024 ** 2)

        assert eta > 30, f"a 500-file tail priced at {eta:.1f}s is not credible"

    def test_matches_the_byte_model_when_no_per_file_cost_shows(self):
        """A perfectly byte-bound run should not be inflated."""
        rate = 100 * 1024 ** 2
        eta = _estimate(elapsed=100, bytes_done=100 * rate,
                        bytes_remaining=50 * rate, files_done=1,
                        files_remaining=1, peak_rate=rate)

        assert eta == pytest.approx(50, rel=0.02)

    def test_peak_rate_is_neutral_when_the_tail_mirrors_the_head(self):
        """Splitting one measured elapsed into two terms must not invent time.

        With the remaining work shaped like the completed work, any split of
        cost between bytes and files reproduces the same total. That the peak
        rate changes nothing here is the model behaving.
        """
        kw = dict(elapsed=100, bytes_done=10 * GB, bytes_remaining=10 * GB,
                  files_done=5, files_remaining=5)

        assert _estimate(peak_rate=0, **kw) == pytest.approx(
            _estimate(peak_rate=(10 * GB / 100) * 2, **kw))

    def test_peak_rate_is_what_prices_a_small_file_tail(self):
        """Without it the per-file cost is invisible and the tail reads as free.

        This is the case that motivated the change: bytes nearly exhausted,
        hundreds of sidecars left. On the batch average alone the residual is
        zero, so the estimate collapses to nothing.
        """
        kw = dict(elapsed=100, bytes_done=10 * GB, bytes_remaining=1024,
                  files_done=5, files_remaining=500)

        assert _estimate(peak_rate=0, **kw) == pytest.approx(0, abs=1)
        assert _estimate(peak_rate=(10 * GB / 100) * 2, **kw) > 60

    @pytest.mark.parametrize("kw", [
        {"elapsed": 0, "bytes_done": 1024},
        {"elapsed": 10, "bytes_done": 0},
    ])
    def test_returns_none_with_nothing_to_measure(self, kw):
        assert _estimate(bytes_remaining=1024, files_done=0, files_remaining=1,
                         peak_rate=0, **kw) is None

    def test_zero_remaining_is_zero(self):
        eta = _estimate(elapsed=100, bytes_done=10 * GB, bytes_remaining=0,
                        files_done=10, files_remaining=0, peak_rate=0)

        assert eta == 0


class TestDisplayDenominatorsAgree:

    def test_banner_leads_with_bytes_when_available(self):
        """The bar and ETA are byte-based; the headline number must match."""
        import pathlib
        html = (pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" /
                "components" / "global_operation_banner.html").read_text(encoding="utf-8")

        # There are two progress-meta blocks; pick the operation one.
        block = html[html.index("status.has_byte_progress"):]
        block = block[:block.index("</div>")]
        assert block.index("bytes_display") < block.index("completed_files"), (
            "file count still leads, which is what made a correct ETA look wrong"
        )
