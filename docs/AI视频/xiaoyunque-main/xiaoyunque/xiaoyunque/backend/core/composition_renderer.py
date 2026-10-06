"""Bounded local ffmpeg process; fixed arguments, verified copies, no network."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from core.content_revisions import fail
from core.media_archive import VIDEO_LIMIT

class CompositionRenderer:
    @staticmethod
    def available():
        return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))

    @staticmethod
    def has_audio(path):
        probe = subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-protocol_whitelist','file',
            '-select_streams','a:0','-show_entries','stream=codec_type','-of','json', str(path)],
            capture_output=True, timeout=10, check=False)
        if probe.returncode != 0 or len(probe.stdout) > 65536:raise ValueError('Invalid audio stream')
        return bool(json.loads(probe.stdout)['streams'])

    @contextmanager
    def render(self, job, archive, stop_event=None):
        stop_event = stop_event or threading.Event()
        if not self.available():fail(503,'COMPOSITION_TOOLS_MISSING','本机视频合成工具尚不可用')
        archive._directory()
        with tempfile.TemporaryDirectory(prefix='.composition-', dir=archive.directory) as temporary, ExitStack() as stack:
            command = [shutil.which('ffmpeg'), '-nostdin','-v','error','-y']
            filters=[]; pairs=[]
            for index, clip in enumerate(job.plan.clips):
                if stop_event.is_set():raise TimeoutError('Local render interrupted')
                source=archive.verify_file(clip.media)
                copied, details=stack.enter_context(archive.prepare(source,'video'))
                if any(details[key] != getattr(clip.media,key) for key in details):raise ValueError('Pinned clip changed')
                command += ['-threads','1','-protocol_whitelist','file','-i',str(copied)]
                w,h,d=job.plan.width,job.plan.height,clip.media.duration
                filters.append(f'[{index}:v:0]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30,trim=duration={d},setpts=PTS-STARTPTS[v{index}]')
                if self.has_audio(copied):
                    filters.append(f'[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={d},asetpts=PTS-STARTPTS[a{index}]')
                else:
                    filters.append(f'anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={d},asetpts=PTS-STARTPTS[a{index}]')
                pairs.append(f'[v{index}][a{index}]')
            filters.append(''.join(pairs)+f'concat=n={len(pairs)}:v=1:a=1[vout][aout]')
            output=Path(temporary)/'film.mp4'
            if stop_event.is_set():raise TimeoutError('Local render interrupted')
            command += ['-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','[vout]','-map','[aout]',
                '-c:v','libx264','-preset','fast','-crf','23','-pix_fmt','yuv420p','-threads','2',
                '-c:a','aac','-b:a','128k','-ar','48000','-ac','2','-map_metadata','-1',
                '-movflags','+faststart','-t',str(job.plan.total_duration),str(output)]
            process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            deadline=time.monotonic()+300
            try:
                while process.poll() is None:
                    if stop_event.wait(.2) or time.monotonic()>deadline or output.exists() and output.stat().st_size>VIDEO_LIMIT:
                        raise TimeoutError('Local render interrupted or limited')
                if process.returncode != 0 or not output.exists():raise ValueError('Local render failed')
                yield output
            finally:
                if process.poll() is None:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait(timeout=5)
