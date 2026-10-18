"""Issue #22 server lifecycle seam; never launches or retries an HIL row."""
from pathlib import Path
from contextlib import contextmanager
import time
import subprocess

try:
    from . import user_owned_https_server_manager as manager
except ImportError:
    import user_owned_https_server_manager as manager


class Issue22FixtureServerHook:
    def __init__(self, spec, state, journal, *, spec_sha256, retry_seconds=30):
        self.spec, self.state = Path(spec), Path(state)
        self.journal, self.spec_sha256 = Path(journal), spec_sha256
        self.retry_seconds = retry_seconds
        self._reservation = None
        self._reservation_used = False

    def before_child(self):
        # Prove the journal is safe before *each* readiness attempt. Retry only
        # infrastructure readiness; never call run_child from this loop.
        deadline = time.monotonic() + self.retry_seconds
        while True:
            manager.safe_journal(self.journal, 'issue22-fixture')
            handoff = (self._reservation if self._reservation is not None
                       and not self._reservation_used else None)
            try:
                ready = manager.operate('ensure', self.spec, self.state,
                                        expected_spec_sha256=self.spec_sha256,
                                        journal_path=self.journal,
                                        reservation=handoff)
                manager.remote.require(ready['state'] == 'running'
                                       and ready['listener_owned']
                                       and ready['tls_verified'], 'HTTPS is not ready')
                return ready
            except (OSError, subprocess.SubprocessError, manager.remote.ManagerError):
                if handoff is not None and handoff.ensure_sent:
                    # The lease can hand off exactly once. A retry is safe only
                    # through a fresh manager ensure, before any row is claimed.
                    self._reservation_used = True
                elif handoff is not None and handoff.poll() is not None:
                    self._reservation_used = True
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
            finally:
                if handoff is not None and (handoff.ensure_sent or handoff.poll() is not None):
                    self._reservation_used = True

    def cleanup_only(self):
        # No journal read: cleanup remains possible after restore/child failure,
        # even when the hardware journal or original spec is unavailable.
        if self.spec_sha256 is None:
            return manager.stop_saved(self.state)
        return manager.operate('stop', self.spec, self.state,
                               expected_spec_sha256=self.spec_sha256)

    @contextmanager
    def child_guard(self):
        # Integration wraps readiness, child execution *and* all post-child
        # assertions (including restore verification) in this one scope.
        try:
            self.before_child()
            yield
        except BaseException:
            try:
                self.cleanup_only()
            except BaseException:
                pass  # Manager's durable cleanup_pending remains actionable.
            raise

    def after_completion(self):
        return self.cleanup_only()

    @contextmanager
    def stage_and_batch_guard(self, server):
        # One remote helper owns the lock and bound listener from its own
        # authoritative preflight, across SCPs, baseline verification and the
        # first managed launch/handoff.
        with manager.remote_reservation(server.manager_spec) as reservation:
            server.stage(reservation)
            with self.batch_guard(reservation=reservation):
                yield self

    @contextmanager
    def batch_guard(self, *, reservation=None):
        # Wrap the complete batch body, including initial journal validation,
        # every child and every post-child restore/result check. Infrastructure
        # cleanup is independent of whether board recovery is still required.
        self._reservation = reservation
        self._reservation_used = False
        try:
            yield self
        except BaseException:
            try:
                self.cleanup_only()
            except BaseException:
                pass  # Preserve the primary error; durable pending survives.
            raise
        else:
            # A successful campaign must report a cleanup failure as pending,
            # without launching another child or changing its completed index.
            self.cleanup_only()
        finally:
            self._reservation = None
            self._reservation_used = False
