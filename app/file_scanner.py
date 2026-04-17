# app/file_scanner.py
from pathlib import Path

# Путь внутри контейнера
BASE_MEDIA = Path("/app/media")

def get_folder_tree(base_path=None):
    """Возвращает дерево папок для выбора"""
    if base_path is None:
        base_path = BASE_MEDIA
    
    tree = {}
    
    if not base_path.exists():
        print(f"⚠️ Папка не найдена: {base_path}")
        return {"error": f"Папка не найдена: {base_path}"}
    
    try:
        for item in base_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                tree[item.name] = {
                    "files": len([f for f in item.iterdir() if f.is_file()]),
                    "subfolders": [sub.name for sub in item.iterdir() if sub.is_dir()]
                }
    except PermissionError as e:
        print(f"⚠️ Нет доступа к папке: {base_path} - {e}")
        return {"error": "Нет доступа"}
    
    return tree

def get_folders_list():
    """Возвращает простой список папок"""
    tree = get_folder_tree()
    if "error" in tree:
        return []
    return list(tree.keys())

def get_files_in_path(folder_name, subfolder=None):
    """Возвращает список файлов из указанной папки"""
    path = BASE_MEDIA / folder_name
    if subfolder:
        path = path / subfolder
    
    if not path.exists():
        return []
    
    valid_ext = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".webm", ".mkv"}
    return [
        str(f) for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in valid_ext
    ]
