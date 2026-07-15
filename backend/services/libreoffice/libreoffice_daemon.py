import os
import subprocess
import time
import socket
import logging
import shutil

logger = logging.getLogger("libreoffice_daemon")

class LibreOfficeDaemon:
    def __init__(self, port: int = 2002):
        self.port = port
        self.process = None
        self.running = False
        
    def _get_soffice_path(self) -> str:
        soffice_path = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice_path:
            for p in [
                "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                "/opt/homebrew/bin/soffice",
                "/usr/local/bin/soffice",
            ]:
                if os.path.exists(p):
                    soffice_path = p
        return soffice_path

    def is_alive(self) -> bool:
        """Pings the URP socket port to check if LibreOffice is responsive."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", self.port))
            s.close()
            return True
        except Exception:
            return False

    def start(self):
        if self.is_alive():
            logger.info(f"LibreOffice daemon already listening on port {self.port}.")
            self.running = True
            return
            
        soffice_path = self._get_soffice_path()
        if not soffice_path:
            raise RuntimeError("LibreOffice executable (soffice) not found on system path.")
            
        profile_dir = os.path.abspath(f"data/ppt/soffice_profile_{self.port}")
        os.makedirs(profile_dir, exist_ok=True)
        profile_url = f"file://{profile_dir}"
        
        cmd = [
            soffice_path,
            "--headless",
            "--invisible",
            "--nocrashreport",
            "--nodefault",
            "--norestore",
            "--nofirststartwizard",
            "--nologo",
            f"-env:UserInstallation={profile_url}",
            f"--accept=socket,host=localhost,port={self.port};urp;"
        ]
        
        logger.info(f"Launching headless LibreOffice daemon: {' '.join(cmd)}")
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )
        
        # Wait for the socket to bind and start accepting connections
        for _ in range(20):
            if self.is_alive():
                logger.info("LibreOffice daemon is fully responsive and accepting socket connections.")
                self.running = True
                return
            time.sleep(0.5)
            
        raise RuntimeError("LibreOffice daemon process launched, but failed to listen on port in time.")

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            logger.info("LibreOffice daemon process terminated.")

    def supervise_loop(self):
        """Monitors socket health periodically and restarts soffice if killed or hung."""
        while self.running:
            if not self.is_alive():
                logger.warning("LibreOffice daemon socket connection dropped! Triggering auto-restart...")
                try:
                    self.stop()
                    self.start()
                except Exception as err:
                    logger.error(f"Watchdog auto-restart failed: {err}")
            time.sleep(5.0)
