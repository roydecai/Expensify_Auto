import json
import os
import re
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

def main():
    if len(sys.argv) < 3:
        print("Usage: python gui_summary.py <log_file> <output_dir> [source_dir]")
        sys.exit(1)

    log_file = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    source_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        sys.exit(1)

    # Parse Log File for Stats
    stats = {
        "total": 0,
        "pass": 0,
        "fail_human": 0,
        "fail_llm": 0
    }
    
    # LLM Stats
    llm_candidates = 0
    llm_fixes_generated = 0
    autofix_status = "Not Triggered"
    
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
        
        # Check validation summary
        # INFO __main__ 校验完成: total=18 pass=17 fail_human=1 fail_llm=0
        match = re.search(r"校验完成: total=(\d+) pass=(\d+) fail_human=(\d+) fail_llm=(\d+)", content)
        if match:
            stats["total"] = int(match.group(1))
            stats["pass"] = int(match.group(2))
            stats["fail_human"] = int(match.group(3))
            stats["fail_llm"] = int(match.group(4))
        
        # Check LLM Candidates (Hard-code failed)
        # LLM 修复轮次: 1/2，任务数: 2
        candidates_matches = re.findall(r"LLM 修复轮次: \d+/\d+，任务数: (\d+)", content)
        if candidates_matches:
            # The first match usually represents the initial set of failed files
            llm_candidates = int(candidates_matches[0])

        # Check LLM Fixes Generated
        # INFO __main__ 已写入修复结果: 2 个
        match_fix = re.search(r"已写入修复结果: (\d+) 个", content)
        if match_fix:
            llm_fixes_generated = int(match_fix.group(1))
            
        # Check Autofix (Learning) Status
        # 自动迭代结果: {status}
        match_autofix = re.search(r"自动迭代结果: (.+)", content)
        if match_autofix:
            autofix_status = match_autofix.group(1).strip()
        elif stats["fail_llm"] == 0:
             autofix_status = "Not Triggered (All issues resolved or escalated)"
        else:
             autofix_status = "Not Triggered (Unknown reason)"

    # Construct LLM Status Text
    llm_status_text = (
        f"Candidates (Hard-code Failed): {llm_candidates}\n"
        f"Fixes Generated: {llm_fixes_generated}\n"
        f"Learning (Autofix): {autofix_status}"
    )

    # Load FAIL_HUMAN details
    fail_human_items = []
    human_review_path = output_dir / "human_review_cases.json"
    if human_review_path.exists():
        try:
            with open(human_review_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                cases = data.get("cases", []) if isinstance(data, dict) else data
                
                for item in cases:
                    if isinstance(item, dict):
                        json_fname = item.get("json_filename", "Unknown")
                        pdf_fname = item.get("pdf_filename")
                        errors = item.get("errors", [])
                        
                        # Extract first error message as reason
                        reason = "Unknown error"
                        if errors and isinstance(errors, list):
                            first_err = errors[0]
                            if isinstance(first_err, dict):
                                reason = first_err.get("message", "Unknown error")
                            elif isinstance(first_err, str):
                                reason = first_err
                        
                        fail_human_items.append({
                            "json": json_fname,
                            "pdf": pdf_fname,
                            "reason": reason
                        })
        except Exception as e:
            print(f"Error reading human_review_cases.json: {e}")

    # Create GUI
    root = tk.Tk()
    root.title("Expensify Auto - Processing Summary")
    root.geometry("800x600")
    
    # Style
    style = ttk.Style()
    style.configure("Bold.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"))

    # Bottom Frame for Close Button (Packed first to ensure visibility)
    bottom_frame = ttk.Frame(root, padding="10")
    bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)
    
    # Close Button centered
    ttk.Button(bottom_frame, text="Close", command=root.destroy).pack(anchor="center")

    # Main Frame
    main_frame = ttk.Frame(root, padding="20")
    main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # Header
    ttk.Label(main_frame, text="Processing Complete", style="Header.TLabel").pack(pady=(0, 10))

    # Stats Section
    stats_frame = ttk.LabelFrame(main_frame, text="Statistics", padding="10")
    stats_frame.pack(fill=tk.X, pady=5)
    
    grid_opts = {"padx": 5, "pady": 2, "sticky": "w"}
    
    ttk.Label(stats_frame, text="Total Processed:").grid(row=0, column=0, **grid_opts)
    ttk.Label(stats_frame, text=str(stats["total"]), style="Bold.TLabel").grid(row=0, column=1, **grid_opts)
    
    ttk.Label(stats_frame, text="Successfully Processed:").grid(row=1, column=0, **grid_opts)
    ttk.Label(stats_frame, text=str(stats["pass"]), style="Bold.TLabel", foreground="green").grid(row=1, column=1, **grid_opts)
    
    ttk.Label(stats_frame, text="Human Intervention Needed:").grid(row=2, column=0, **grid_opts)
    fail_count_style = "Bold.TLabel"
    fail_fg = "red" if stats["fail_human"] > 0 else "black"
    lbl_fail = ttk.Label(stats_frame, text=str(stats["fail_human"]), style=fail_count_style)
    lbl_fail.configure(foreground=fail_fg)
    lbl_fail.grid(row=2, column=1, **grid_opts)

    # LLM Section
    llm_frame = ttk.LabelFrame(main_frame, text="LLM Status", padding="10")
    llm_frame.pack(fill=tk.X, pady=5)
    
    # Use a multiline label for status
    lbl_llm = ttk.Label(llm_frame, text=llm_status_text, style="Bold.TLabel", justify=tk.LEFT)
    lbl_llm.pack(anchor="w", padx=5)

    # Failures List
    if stats["fail_human"] > 0:
        fail_frame = ttk.LabelFrame(main_frame, text="Files Requiring Attention", padding="10")
        fail_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=5)

        # Treeview container
        tree_frame = ttk.Frame(fail_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("file", "reason")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        tree.heading("file", text="PDF File")
        tree.heading("reason", text="Intervention Reason")
        
        tree.column("file", width=300, minwidth=200)
        tree.column("reason", width=400, minwidth=200)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        for item in fail_human_items:
            # Prefer PDF filename, fallback to JSON filename
            display_name = item["pdf"] if item["pdf"] else item["json"]
            tree.insert("", tk.END, values=(display_name, item["reason"]))
            
        def open_file(event):
            selection = tree.selection()
            if not selection:
                return
                
            item_id = selection[0]
            values = tree.item(item_id, "values")
            fname = values[0]
            
            target_path = None
            
            # 1. Try finding PDF in source_dir
            if source_dir:
                candidate = source_dir / fname
                if candidate.exists():
                    target_path = candidate
                # Check for JSON->PDF mapping if direct match failed
                elif fname.lower().endswith(".json"):
                     # Try to strip _extracted_revised.json pattern
                     pdf_name = None
                     if "_extracted_revised.json" in fname:
                         pdf_name = fname.replace("_extracted_revised.json", ".pdf")
                     elif fname.lower().endswith(".json"):
                         pdf_name = fname[:-5] + ".pdf"
                     
                     if pdf_name:
                         candidate = source_dir / pdf_name
                         if candidate.exists():
                             target_path = candidate
            
            # 2. Try finding file in output_dir (maybe it's a JSON or PDF was copied there?)
            if not target_path and output_dir:
                candidate = output_dir / fname
                if candidate.exists():
                    target_path = candidate
            
            # 3. If filename ends with .pdf but not found, check if there is a corresponding JSON in output_dir
            # (Just as a fallback to show SOMETHING)
            if not target_path and output_dir and fname.lower().endswith(".pdf"):
                 json_name = fname.replace(".pdf", "_extracted_revised.json")
                 candidate = output_dir / json_name
                 if candidate.exists():
                     target_path = candidate

            if target_path and target_path.exists():
                try:
                    # Use explorer /select to highlight the file
                    subprocess.Popen(f'explorer /select,"{target_path}"')
                except Exception as e:
                    messagebox.showerror("Error", f"Could not open file location: {e}")
            else:
                # Fallback: Open the directory
                if source_dir and source_dir.exists():
                     subprocess.Popen(f'explorer "{source_dir}"')
                elif output_dir and output_dir.exists():
                     subprocess.Popen(f'explorer "{output_dir}"')

        tree.bind('<Double-1>', open_file)
        
        # Label for instruction (Packed AFTER tree_frame, at bottom of fail_frame)
        ttk.Label(fail_frame, text="Double-click to locate PDF file", font=("Segoe UI", 8, "italic")).pack(side=tk.BOTTOM, anchor="w", pady=(5, 0))

    # Close Button (Removed from here as it's now in bottom_frame)
    # ttk.Button(main_frame, text="Close", command=root.destroy).pack(pady=10)

    # Bring to front
    root.lift()
    root.attributes('-topmost', True)
    root.after_idle(root.attributes, '-topmost', False)
    
    root.mainloop()

if __name__ == "__main__":
    main()
