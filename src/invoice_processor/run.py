import sys
import os
import subprocess

def main():
    """
    Launcher for the Invoice Processor.
    """
    # Get the directory where this script is located (invoice_processor folder)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Path to the main entry point (sibling file)
    main_script = os.path.join(current_dir, 'main.py')
    
    if not os.path.exists(main_script):
        print(f"Error: Main script not found at {main_script}")
        sys.exit(1)

    # Construct the command
    cmd = [sys.executable, "-X", "utf8", main_script] + sys.argv[1:]
    
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("Starting Invoice Processor...")
    print(f"Target: {main_script}")
    print("-" * 40)
    
    # Run the command
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)

if __name__ == "__main__":
    main()
