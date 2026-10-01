"""YT Narrator Desktop App (PyQt5)."""

import sys
import json
import requests
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QLineEdit, QPushButton, QTextEdit, QTableWidget,
    QTableWidgetItem, QDialog, QScrollArea, QSpinBox, QMessageBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor


class SettingsDialog(QDialog):
    """Settings panel for API keys."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setGeometry(100, 100, 600, 500)
        
        layout = QVBoxLayout()
        
        # Gemini API key
        gemini_label = QLabel("Gemini API Key:")
        self.gemini_input = QLineEdit()
        self.gemini_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(gemini_label)
        layout.addWidget(self.gemini_input)
        
        # HF API keys (up to 50)
        hf_label = QLabel("HuggingFace API Keys (comma-separated or one per line):")
        self.hf_input = QTextEdit()
        self.hf_input.setPlaceholderText("hf_key1\nhf_key2\nhf_key3\n...")
        self.hf_input.setMinimumHeight(150)
        layout.addWidget(hf_label)
        layout.addWidget(self.hf_input)
        
        # Server URL
        url_label = QLabel("API Server URL:")
        self.url_input = QLineEdit()
        self.url_input.setText("http://localhost:8000")
        layout.addWidget(url_label)
        layout.addWidget(self.url_input)
        
        # Save button
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.load_settings()
    
    def load_settings(self):
        """Load from .env or config file."""
        config_path = Path.home() / ".yt-narrator" / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
                self.gemini_input.setText(config.get("gemini_api_key", ""))
                hf_keys = config.get("hf_api_keys", [])
                self.hf_input.setPlainText("\n".join(hf_keys))
                self.url_input.setText(config.get("api_url", "http://localhost:8000"))
    
    def save_settings(self):
        """Save settings to config file."""
        config_dir = Path.home() / ".yt-narrator"
        config_dir.mkdir(exist_ok=True)
        
        config = {
            "gemini_api_key": self.gemini_input.text(),
            "hf_api_keys": [k.strip() for k in self.hf_input.toPlainText().split("\n") if k.strip()],
            "api_url": self.url_input.text()
        }
        
        config_path = config_dir / "config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        
        QMessageBox.information(self, "Success", f"Settings saved ({len(config['hf_api_keys'])} HF keys)")


class JobWorker(QThread):
    """Fetch job status in background."""
    
    status_updated = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    
    def __init__(self, job_id, api_url):
        super().__init__()
        self.job_id = job_id
        self.api_url = api_url
        self.running = True
    
    def run(self):
        """Poll job status."""
        while self.running:
            try:
                resp = requests.get(f"{self.api_url}/job/{self.job_id}", timeout=5)
                if resp.status_code == 200:
                    self.status_updated.emit(resp.json())
                else:
                    self.error_occurred.emit(f"Error: {resp.status_code}")
            except Exception as e:
                self.error_occurred.emit(str(e))
            
            self.msleep(2000)  # Poll every 2 seconds
    
    def stop(self):
        """Stop polling."""
        self.running = False


class DesktopApp(QMainWindow):
    """YT Narrator Desktop Application."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YT Narrator — Auto YouTube Videos")
        self.setGeometry(100, 100, 900, 700)
        
        self.config_path = Path.home() / ".yt-narrator" / "config.json"
        self.api_url = "http://localhost:8000"
        self.current_job_id = None
        self.job_worker = None
        
        self.load_config()
        self.init_ui()
    
    def load_config(self):
        """Load saved config."""
        if self.config_path.exists():
            with open(self.config_path) as f:
                config = json.load(f)
                self.api_url = config.get("api_url", "http://localhost:8000")
    
    def init_ui(self):
        """Initialize UI."""
        tabs = QTabWidget()
        
        # Tab 1: Submit
        tab_submit = self.create_submit_tab()
        tabs.addTab(tab_submit, "Submit Job")
        
        # Tab 2: Monitor
        tab_monitor = self.create_monitor_tab()
        tabs.addTab(tab_monitor, "Monitor")
        
        # Tab 3: Settings
        tab_settings = self.create_settings_tab()
        tabs.addTab(tab_settings, "Settings")
        
        self.setCentralWidget(tabs)
        self.show()
    
    def create_submit_tab(self):
        """Create job submission tab."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        label = QLabel("Enter Topic/Title:")
        font = QFont()
        font.setPointSize(12)
        label.setFont(font)
        
        self.topic_input = QTextEdit()
        self.topic_input.setPlaceholderText("e.g., 'History of Ancient Egypt' or 'How Photosynthesis Works'")
        self.topic_input.setMinimumHeight(100)
        
        submit_btn = QPushButton("Submit Job")
        submit_btn.setFont(font)
        submit_btn.clicked.connect(self.submit_job)
        
        self.status_label = QLabel("")
        
        layout.addWidget(label)
        layout.addWidget(self.topic_input)
        layout.addWidget(submit_btn)
        layout.addWidget(self.status_label)
        layout.addStretch()
        
        widget.setLayout(layout)
        return widget
    
    def create_monitor_tab(self):
        """Create job monitoring tab."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # Job ID input
        id_layout = QHBoxLayout()
        id_label = QLabel("Job ID:")
        self.monitor_id_input = QLineEdit()
        monitor_btn = QPushButton("Monitor")
        monitor_btn.clicked.connect(self.start_monitoring)
        
        id_layout.addWidget(id_label)
        id_layout.addWidget(self.monitor_id_input)
        id_layout.addWidget(monitor_btn)
        
        layout.addLayout(id_layout)
        
        # Status display
        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.status_display.setMinimumHeight(300)
        
        # Download button
        download_btn = QPushButton("Download Video")
        download_btn.clicked.connect(self.download_video)
        
        layout.addWidget(QLabel("Job Status:"))
        layout.addWidget(self.status_display)
        layout.addWidget(download_btn)
        layout.addStretch()
        
        widget.setLayout(layout)
        return widget
    
    def create_settings_tab(self):
        """Create settings tab."""
        widget = QWidget()
        layout = QVBoxLayout()
        
        label = QLabel("Configure API Keys and Server")
        font = QFont()
        font.setPointSize(12)
        label.setFont(font)
        
        settings_btn = QPushButton("Open Settings Panel")
        settings_btn.clicked.connect(self.open_settings)
        
        info_label = QLabel(
            "Add up to 50 HuggingFace API keys for unlimited generation quota.\n"
            "Keys are stored locally in ~/.yt-narrator/config.json"
        )
        info_label.setStyleSheet("color: #666;")
        
        layout.addWidget(label)
        layout.addWidget(settings_btn)
        layout.addWidget(info_label)
        layout.addStretch()
        
        widget.setLayout(layout)
        return widget
    
    def submit_job(self):
        """Submit new job."""
        topic = self.topic_input.toPlainText().strip()
        if not topic:
            QMessageBox.warning(self, "Error", "Please enter a topic")
            return
        
        try:
            resp = requests.post(
                f"{self.api_url}/job/submit",
                json={"title": topic},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                self.current_job_id = data["job_id"]
                self.status_label.setText(f"✓ Job submitted: {self.current_job_id}")
                self.topic_input.clear()
                QMessageBox.information(self, "Success", f"Job ID: {self.current_job_id}")
            else:
                QMessageBox.critical(self, "Error", f"Server error: {resp.status_code}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
    
    def start_monitoring(self):
        """Start monitoring job."""
        job_id = self.monitor_id_input.text().strip()
        if not job_id:
            QMessageBox.warning(self, "Error", "Please enter a job ID")
            return
        
        if self.job_worker:
            self.job_worker.stop()
            self.job_worker.wait()
        
        self.current_job_id = job_id
        self.job_worker = JobWorker(job_id, self.api_url)
        self.job_worker.status_updated.connect(self.update_status)
        self.job_worker.error_occurred.connect(self.show_error)
        self.job_worker.start()
    
    def update_status(self, data):
        """Update job status display."""
        status = data.get("status", "unknown")
        stage = data.get("stage", "")
        progress = data.get("progress", "")
        
        text = f"""
Job ID: {self.current_job_id}
Status: {status}
Stage: {stage}
Progress: {progress}

Video: {data.get('output_video', 'N/A')}
Cost: ${data.get('actual_cost_usd', 0):.2f}
"""
        self.status_display.setText(text.strip())
    
    def show_error(self, error):
        """Show error message."""
        self.status_display.setText(f"Error: {error}")
    
    def download_video(self):
        """Download video from completed job."""
        if not self.current_job_id:
            QMessageBox.warning(self, "Error", "No job selected")
            return
        
        try:
            url = f"{self.api_url}/job/{self.current_job_id}/video"
            resp = requests.get(url, timeout=30)
            
            if resp.status_code == 200:
                save_path = Path.home() / "Downloads" / f"{self.current_job_id}.mp4"
                save_path.write_bytes(resp.content)
                QMessageBox.information(self, "Success", f"Video saved: {save_path}")
            else:
                QMessageBox.critical(self, "Error", f"Download failed: {resp.status_code}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
    
    def open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(self)
        dialog.exec_()
        self.load_config()


def main():
    app = QApplication(sys.argv)
    window = DesktopApp()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
