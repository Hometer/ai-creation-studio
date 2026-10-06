"""Recover only durable, explicitly confirmed initial document jobs."""
import logging,threading
logger=logging.getLogger(__name__)
class InitialWorker:
    def __init__(self,drafts):
        self.drafts=drafts;self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self.run,name='confirmed-initial-draft-worker',daemon=True)
    def start(self):self.thread.start()
    def stop(self):self.stop_event.set();self.thread.join(timeout=1)
    def run(self):
        while not self.stop_event.is_set():
            try:self.drafts.tick()
            except Exception as exc:logger.warning('Initial draft recovery paused (%s)',type(exc).__name__)
            self.stop_event.wait(2)
