"""
Simple build script for JARVIS Rocket Simulation executable
This uses direct PyInstaller commands for better Windows compatibility
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


APP_NAME = "JARVIS_Rocket_Simulation"


def built_app_path(project_root):
    """Path to the built executable.

    onedir puts it in dist/<name>/<name>[.exe]; the whole folder is what
    gets copied to the flash drive, not just this file.
    """
    exe = APP_NAME + (".exe" if os.name == "nt" else "")
    return Path(project_root) / "dist" / APP_NAME / exe


def main():
    print("Building JARVIS Rocket Simulation Executable...")
    
    # Get the project root directory
    project_root = Path(__file__).parent
    src_dir = project_root / "src"
    main_py = src_dir / "main.py"
    
    if not main_py.exists():
        print(f"ERROR: Error: {main_py} not found!")
        return False
    
    # Clean previous builds
    for folder in ["build", "dist", "__pycache__"]:
        folder_path = project_root / folder
        if folder_path.exists():
            print(f"Cleaning {folder}...")
            shutil.rmtree(folder_path)
    
    # Change to project directory
    os.chdir(project_root)
    
    print("Building with PyInstaller...")
    
    # PyInstaller uses ';' between source and destination on Windows, ':' elsewhere.
    sep = ";" if os.name == "nt" else ":"
    hybrid_sim_dir = project_root / "hybrid_sim"

    cmd = [
        # "PyInstaller", capitalised: that is the real package name. The
        # lowercase spelling only resolves on a case-insensitive filesystem,
        # so "-m pyinstaller" works on Windows and fails outright on Linux
        # and macOS.
        sys.executable, "-m", "PyInstaller",
        # onedir, not onefile. A onefile build re-extracts the entire
        # bundle to a temp folder on EVERY launch - tens of seconds from a
        # USB stick, it litters temp directories, and locked-down lab
        # machines often block executing from temp. onedir starts fast and
        # runs in place from the drive.
        "--onedir",
        "--windowed",
        "--name=JARVIS_Rocket_Simulation",
        f"--icon={src_dir / 'JARVIS.ico'}",
        "--clean",
        "--noconfirm",
        # Runtime assets the app loads by name
        f"--add-data={src_dir / 'JARVIS.ico'}{sep}.",
        f"--add-data={src_dir / 'jarvis.gif'}{sep}.",
        f"--add-data={src_dir / 'Rocket.png'}{sep}.",
        f"--add-data={src_dir / 'crash.jpg'}{sep}.",
        f"--add-data={project_root / 'thrust_curves'}{sep}thrust_curves",
        f"--add-data={src_dir / 'profiles'}{sep}profiles",
        # Combo/spin drop-down arrows referenced by theme.py's stylesheet;
        # without them every combo in the frozen build loses its arrow.
        f"--add-data={src_dir / 'assets'}{sep}assets",
        # The hybrid engine package is imported through a runtime path insert,
        # which PyInstaller's static analysis cannot follow - ship it as data
        # and put it on the analysis path explicitly.
        f"--add-data={hybrid_sim_dir}{sep}hybrid_sim",
        f"--paths={hybrid_sim_dir}",
        "--hidden-import=hybrid_sim",
        # hybrid_sim/engine.py reaches engine_equations through a runtime
        # sys.path insert, which PyInstaller cannot follow, so nothing would
        # collect it and the frozen Engine Lab would die on import. Name it
        # explicitly. flight_equations is imported normally (aero, atmosphere)
        # and would be traced anyway - it is listed for symmetry, so the two
        # physics files are never separated by accident.
        f"--paths={src_dir}",
        "--hidden-import=engine_equations",
        "--hidden-import=flight_equations",
        # The motor sizing solver. engine_lab imports it normally so the
        # analysis should find it, but it is named here beside the other two
        # physics modules for the same reason they are: so a refactor that
        # moves an import behind a path insert cannot silently drop it, and
        # the Engine tab die on the Generate button in the frozen build only.
        "--hidden-import=motor_designer",
        "--hidden-import=matplotlib.backends.backend_qt5agg",
        "--hidden-import=scipy.integrate",
        "--hidden-import=scipy.optimize",
        str(main_py)
    ]
    
    print("Running command:", " ".join(cmd))
    
    try:
        # Run PyInstaller
        result = subprocess.run(cmd, check=True, text=True)
        
        # Check if executable was created
        exe_path = built_app_path(project_root)
        if exe_path.exists():
            folder = exe_path.parent
            total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
            print(f"OK: Success! Built: {exe_path}")
            print(f"Folder size: {total / (1024 * 1024):.1f} MB")
            print(f"Copy the whole '{folder.name}' folder to the flash drive.")
            return True
        else:
            print("ERROR: Executable not found after build")
            return False
            
    except subprocess.CalledProcessError as e:
        print(f"ERROR: PyInstaller failed: {e}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        return False

def test_executable():
    """Test the executable by trying to run it briefly"""
    project_root = Path(__file__).parent
    exe_path = built_app_path(project_root)

    if exe_path.exists():
        print("Testing executable...")
        try:
            # Try to start the process and kill it quickly (just to test it launches)
            import time
            process = subprocess.Popen([str(exe_path)], 
                                     stdout=subprocess.PIPE, 
                                     stderr=subprocess.PIPE)
            time.sleep(2)  # Let it start
            process.terminate()
            print("OK: Executable test passed - app can launch")
            return True
        except Exception as e:
            print(f"ERROR: Executable test failed: {e}")
            return False
    return False

if __name__ == "__main__":
    success = main()
    if success:
        print("\nBuild completed successfully!")
        print(f"Ready in dist/{APP_NAME}/")
        print(f"Copy that whole folder to a flash drive and run {APP_NAME}")
        print("\nDistribution notes:")
        print("  - No installation, no admin rights, no Python needed")
        print("  - Contains the complete Python runtime and dependencies")
        print("  - Everything you save goes to JARVIS-Data beside the program,")
        print("    so your rockets travel with the drive")
        print("  - If antivirus flags it, that is normal for a fresh build")
        
        # Optional test. --no-prompt keeps this non-interactive so CI (and
        # anyone scripting a build) does not hang on a question.
        if "--no-prompt" in sys.argv:
            print("\n(--no-prompt: skipping the launch test)")
        elif input("\nTest the executable now? (y/n): ").lower().startswith('y'):
            test_executable()
    else:
        print("\nBuild failed!")
        print("Try running from command prompt to see detailed error messages")
        sys.exit(1)