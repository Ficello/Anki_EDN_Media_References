"""
Script utilitaire pour empaqueter l'addon au format .ankiaddon
"""

import os
import zipfile

def package_addon():
    addon_dir = os.path.dirname(os.path.abspath(__file__))
    addon_name = "Media_References.ankiaddon"
    output_path = os.path.join(addon_dir, addon_name)
    
    # Fichiers et dossiers à exclure
    excluded_names = {
        '.git', '.gitignore', '__pycache__', '.pytest_cache',
        'tests', 'scratch', 'temp', addon_name, 'Anki_EDN_Media_References.ankiaddon'
    }
    excluded_exts = {'.pyc', '.pyo', '.pyd', '.zip', '.tar', '.gz'}

    print(f"Packaging addon from: {addon_dir}")
    print(f"Destination: {output_path}")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(addon_dir):
            dirs[:] = [d for d in dirs if d not in excluded_names]
            
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if file in excluded_names or ext in excluded_exts or file.endswith('.ankiaddon'):
                    continue
                    
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, addon_dir)
                
                print(f"Adding: {rel_path}")
                zipf.write(full_path, rel_path)

    print("\nPackage created successfully!\n")

if __name__ == "__main__":
    package_addon()
