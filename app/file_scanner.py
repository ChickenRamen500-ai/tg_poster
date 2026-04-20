# app/file_scanner.py
from pathlib import Path
import os
from PIL import Image

# Путь внутри контейнера
BASE_MEDIA = Path(os.getenv("MEDIA_PATH", "/app/media"))
BASE_ERRORS = BASE_MEDIA / "errors"

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

def convert_avif_to_jpg(avif_path):
    """Конвертирует AVIF файл в JPG. Возвращает путь к JPG файлу или None при ошибке."""
    try:
        jpg_path = avif_path.with_suffix('.jpg')
        with Image.open(avif_path) as img:
            # Конвертируем в RGB (AVIF может иметь альфа-канал)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.save(jpg_path, 'JPEG', quality=95)
        return str(jpg_path)
    except Exception as e:
        print(f"⚠️ Ошибка конвертации AVIF: {e}")
        return None

def move_file_to_errors(filepath, error_msg=""):
    """Перемещает файл в папку errors с сохранением структуры"""
    try:
        file_path = Path(filepath)
        rel_path = file_path.relative_to(BASE_MEDIA)
        dest = BASE_ERRORS / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        if file_path.exists() and not dest.exists():
            file_path.rename(dest)
            
            # Записываем информацию об ошибке в .txt файл рядом
            error_info_path = dest.with_suffix(dest.suffix + ".error.txt")
            with open(error_info_path, "w", encoding="utf-8") as f:
                f.write(f"Файл: {filepath}\n")
                f.write(f"Ошибка: {error_msg}\n")
                import time
                f.write(f"Дата: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            return True
        elif dest.exists():
            return True
    except Exception as e:
        print(f"⚠️ Не удалось переместить в errors: {e}")
    return False

def get_files_in_path(folder_name, subfolder=None):
    """Возвращает список файлов из указанной папки. AVIF конвертируется в JPG, оригинал перемещается в errors."""
    path = BASE_MEDIA / folder_name
    if subfolder:
        path = path / subfolder
    
    if not path.exists():
        return []
    
    valid_ext = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".webm", ".mkv"}
    avif_ext = {".avif"}
    
    result_files = []
    
    for f in path.iterdir():
        if not f.is_file():
            continue
        
        suffix = f.suffix.lower()
        
        # Обработка AVIF файлов
        if suffix in avif_ext:
            print(f"🔄 Найден AVIF файл: {f}, конвертируем в JPG...")
            jpg_path = convert_avif_to_jpg(f)
            if jpg_path:
                print(f"✅ AVIF сконвертирован: {jpg_path}")
                result_files.append(jpg_path)
                # Перемещаем оригинал AVIF в errors
                move_file_to_errors(str(f), "AVIF конвертирован в JPG")
                print(f"📁 Оригинал AVIF перемещён в errors")
            else:
                print(f"❌ Ошибка конвертации AVIF, перемещаем в errors")
                move_file_to_errors(str(f), "Ошибка конвертации AVIF в JPG")
        elif suffix in valid_ext:
            result_files.append(str(f))
    
    return result_files
