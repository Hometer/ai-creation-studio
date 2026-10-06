"""Recover approved free jobs; stop only this worker's local render processes."""
import logging
import threading
logger=logging.getLogger(__name__)
class CompositionWorker:
    def __init__(self,service):
        self.service=service
        self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self.run,name='confirmed-composition-worker',daemon=True)
    def start(self):self.thread.start()
    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=6)
    def run(self):
        while not self.stop_event.is_set():
            try:self.service.tick(self.stop_event)
            except Exception as exc:logger.warning('Local film recovery paused (%s)',type(exc).__name__)
            self.stop_event.wait(2)
