import tkinter as tk
from tkinter import ttk, scrolledtext
import subprocess
import threading
import sys
import os
import queue
from pathlib import Path

class ProcessRunnerGUI:
    def __init__(self, root, cmd, log_file, source_dir, output_dir):
        self.root = root
        self.cmd = cmd
        self.log_file = log_file
        self.source_dir = Path(source_dir)
        self.output_dir = output_dir
        self.process = None
        self.queue = queue.Queue()
        
        self.root.title("Expensify Auto - Processing")
        self.root.geometry("700x500")
        
        # Calculate total files for progress bar
        self.total_files = self.count_pdf_files()
        self.processed_count = 0
        
        self.create_widgets()
        self.start_process()
        self.check_queue()

    def count_pdf_files(self):
        count = 0
        if self.source_dir.exists():
            for _ in self.source_dir.glob("*.pdf"):
                count += 1
        return count if count > 0 else 1  # Avoid division by zero

    def create_widgets(self):
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Status Label
        self.status_var = tk.StringVar(value="Initializing...")
        ttk.Label(main_frame, textvariable=self.status_var, font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 5))
        
        # Progress Bar
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=self.total_files)
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # Log Area
        ttk.Label(main_frame, text="Log Output:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.log_text = scrolledtext.ScrolledText(main_frame, height=15, font=("Consolas", 9), state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Close Button (Initially disabled or hidden)
        self.close_btn = ttk.Button(main_frame, text="Close", command=self.root.destroy, state='disabled')
        self.close_btn.pack(pady=(10, 0))

    def start_process(self):
        threading.Thread(target=self.run_process_thread, daemon=True).start()

    def run_process_thread(self):
        # Open log file for writing
        try:
            with open(self.log_file, "w", encoding="utf-8") as f_log:
                # Start subprocess
                # Ensure python output is unbuffered or flush often? 
                # We use -u for unbuffered python output
                final_cmd = self.cmd
                # Insert -u after python executable if present, but cmd is list of strings
                # Actually, env var PYTHONUNBUFFERED=1 is better.
                env = os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"
                
                self.process = subprocess.Popen(
                    final_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    universal_newlines=True,
                    env=env
                )
                
                for line in self.process.stdout:
                    self.queue.put(("log", line))
                    f_log.write(line)
                    f_log.flush()
                
                self.process.wait()
                self.queue.put(("done", self.process.returncode))
                
        except Exception as e:
            self.queue.put(("error", str(e)))

    def check_queue(self):
        try:
            while True:
                msg_type, content = self.queue.get_nowait()
                
                if msg_type == "log":
                    self.update_log(content)
                    self.parse_progress(content)
                elif msg_type == "done":
                    self.on_process_complete(content)
                elif msg_type == "error":
                    self.update_log(f"ERROR: {content}\n")
                    self.status_var.set("Error occurred")
                    self.close_btn.config(state='normal')
                    
        except queue.Empty:
            pass
        
        self.root.after(100, self.check_queue)

    def update_log(self, line):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def parse_progress(self, line):
        # Check for specific log patterns to update progress
        # "INFO pdf_extraction_service 开始处理: filename"
        # "INFO pdf_extraction_service 完成处理: filename"
        if "开始处理:" in line:
            parts = line.split("开始处理:")
            if len(parts) > 1:
                filename = parts[1].strip()
                self.status_var.set(f"Processing: {filename}")
        
        elif "完成处理:" in line:
            self.processed_count += 1
            self.progress_var.set(self.processed_count)
            # Update percentage in status?
            percent = int((self.processed_count / self.total_files) * 100)
            self.status_var.set(f"Processed {self.processed_count}/{self.total_files} ({percent}%)")

        # LLM Task Handling
        elif "LLM 修复轮次:" in line and "任务数:" in line:
            # INFO __main__ LLM 修复轮次: 1/2，任务数: 2
            try:
                parts = line.split("任务数:")
                if len(parts) > 1:
                    task_count = int(parts[1].strip())
                    # Only add tasks in the first round to avoid double counting if logic changes,
                    # but current logic is iterative.
                    # Actually, main.py logs this for each round.
                    # If we have multiple rounds, we might add tasks multiple times if we are not careful.
                    # However, total_files is the max of the progress bar.
                    # If round 1 has 2 tasks, we add 2.
                    # If round 2 has 1 task (remaining), we shouldn't add it again to the total?
                    # Or should we?
                    # Let's check main.py logic.
                    # It loops rounds.
                    # Round 1: finds 2 fails. Logs "Round 1, tasks: 2". Processing 2 tasks.
                    # Round 2: finds 1 fail remaining. Logs "Round 2, tasks: 1". Processing 1 task.
                    # If we add them all, the progress bar will extend.
                    # Since "processed_count" increments on "LLM 任务完成", which happens for each task in each round.
                    # So yes, we should add task_count to total_files for EACH round.
                    self.total_files += task_count
                    self.progress.configure(maximum=self.total_files)
                    self.status_var.set(f"Adding {task_count} LLM tasks... (Total: {self.total_files})")
            except ValueError:
                pass

        elif "LLM 任务完成:" in line:
            self.processed_count += 1
            self.progress_var.set(self.processed_count)
            percent = int((self.processed_count / self.total_files) * 100) if self.total_files > 0 else 0
            self.status_var.set(f"Processed {self.processed_count}/{self.total_files} ({percent}%)")

    def on_process_complete(self, returncode):
        if returncode == 0:
            self.status_var.set("Processing Complete!")
            self.progress_var.set(self.total_files)
            self.close_btn.config(state='normal')
            
            # Automatically open summary after a brief delay
            self.root.after(1000, self.open_summary)
        else:
            self.status_var.set(f"Processing Failed with code {returncode}")
            self.close_btn.config(state='normal')

    def open_summary(self):
        self.root.destroy()
        # Launch the summary script
        summary_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_summary.py")
        # Pass source_dir as the 3rd argument
        subprocess.Popen([sys.executable, summary_script, self.log_file, self.output_dir, str(self.source_dir)])

def main():
    # Parse args manually to extract --log-file
    # Args expected: src/gui_runner.py source_dir --output-dir output_dir --log-file log_file
    
    args = sys.argv[1:]
    log_file = None
    source_dir = None
    output_dir = None
    
    # Extract --log-file
    if "--log-file" in args:
        idx = args.index("--log-file")
        if idx + 1 < len(args):
            log_file = args[idx+1]
            # Remove from args to pass clean args to run.py
            del args[idx:idx+2]
            
    # Extract source_dir (first positional arg)
    for arg in args:
        if not arg.startswith("-"):
            source_dir = arg
            break
            
    # Extract output_dir
    if "--output-dir" in args:
        idx = args.index("--output-dir")
        if idx + 1 < len(args):
            output_dir = args[idx+1]

    if not log_file:
        print("Error: --log-file argument is required")
        sys.exit(1)

    # Construct command for run.py
    # Assuming run.py is in src/invoice_processor/run.py relative to this script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    run_script = os.path.join(current_dir, "invoice_processor", "run.py")
    
    cmd = [sys.executable, "-u", run_script] + args

    root = tk.Tk()
    app = ProcessRunnerGUI(root, cmd, log_file, source_dir, output_dir)
    root.mainloop()

if __name__ == "__main__":
    main()
