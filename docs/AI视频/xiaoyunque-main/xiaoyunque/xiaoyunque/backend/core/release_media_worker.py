"""Finite per-task reads; a daemon restores approved checkpoints after restart."""
import logging
import threading

logger=logging.getLogger(__name__)
class MediaWorker:
    def __init__(self,media):
        self.media=media;self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self.run,name='confirmed-media-worker',daemon=True)
    def start(self):self.thread.start()
    def stop(self):self.stop_event.set();self.thread.join(timeout=1)
    def run(self):
        while not self.stop_event.is_set():
            try:self.media.tick()
            except Exception as exc:logger.warning('Confirmed task recovery paused (%s)',type(exc).__name__)
            self.stop_event.wait(2)
