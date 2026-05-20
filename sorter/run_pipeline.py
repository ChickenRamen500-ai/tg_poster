import os, json, sys, time
import onnxruntime as ort
import numpy as np
import pandas as pd
from pathlib import Path
from PIL import Image
from tqdm import tqdm
import imagehash
import shutil

# ================= КОНФИГУРАЦИЯ =================
SOURCE = Path("/workspace/source")
OUTPUT = Path("/workspace/sorted")
TAGS_JSON = OUTPUT / "tags.json"
HASH_JSON = OUTPUT / "hash_db.json"

MODEL_NAME = "model.onnx"
TAGS_CSV = "selected_tags.csv"
THRESHOLDS_CSV = "thresholds.csv"
CATEGORIES_JSON = "categories.json"

SUPPORTED = {'.jpg', '.jpeg', '.png', '.jfif', '.webp', '.avif'}
BATCH_SIZE = 4  # Уменьшен для большой модели (1.27 GB)

# ImageNet нормализация для PixAI-Tagger
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

def get_images():
    return [f for f in SOURCE.rglob('*') if f.suffix.lower() in SUPPORTED and f.is_file()]

def compute_hash(path):
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except:
        return None

def stage1_dedup(files):
    print("🔍 Stage 1: Deduplication & Set Detection")
    db = {"exact": {}, "sets": [], "masters": []}
    
    for f in tqdm(files, desc="Hashing"):
        h = compute_hash(f)
        if not h: continue
        db["exact"].setdefault(h, []).append(str(f))

    hash_list = list(db["exact"].keys())
    for i in tqdm(range(len(hash_list)), desc="Clustering"):
        h1 = imagehash.hex_to_hash(hash_list[i])
        for j in range(i+1, len(hash_list)):
            h2 = imagehash.hex_to_hash(hash_list[j])
            dist = h1 - h2
            if 0 < dist <= 6:
                db["sets"].append((hash_list[i], hash_list[j]))

    for h, paths in db["exact"].items():
        master = max(paths, key=lambda p: os.path.getsize(p))
        db["masters"].append(master)
        for p in paths:
            if p != master:
                db.setdefault("dupes", []).append(p)

    with open(HASH_JSON, 'w') as f: json.dump(db, f, indent=2)
    print(f"✅ Unique: {len(db['exact'])} | Dupes: {len(db.get('dupes', []))} | Sets: {len(db['sets'])}")
    return db

def load_metadata():
    """Загружает теги, пороги и категории"""
    tags_df = pd.read_csv(f"/workspace/{TAGS_CSV}")
    thresholds_df = pd.read_csv(f"/workspace/{THRESHOLDS_CSV}")
    
    # categories.json — это СПИСОК [{name, category}, ...], конвертируем в словарь
    with open(f"/workspace/{CATEGORIES_JSON}") as f:
        categories_list = json.load(f)
    categories = {item['name']: {'category': item['category']} for item in categories_list}
    
    # Создаём словари: tag_name -> {threshold, category}
    tag_info = {}
    for _, row in thresholds_df.iterrows():
        tag_name = row['name']
        tag_info[tag_name] = {
            'threshold': row.get('threshold', 0.3),
            'category': categories.get(tag_name, {}).get('category', 0)
        }
    
    # Список тегов в порядке индексов модели
    tags_list = tags_df['name'].astype(str).tolist()
    
    print(f"✅ Loaded {len(tags_list)} tags with thresholds")
    return tags_list, tag_info

def preprocess_pixai(img_path, target_size=448):
    """
    Препроцессинг для PixAI-Tagger-v0.9:
    1. RGBA -> RGB на белом фоне
    2. Паддинг до квадрата белым
    3. Ресайз до target_size (BICUBIC)
    4. Нормализация ImageNet
    5. Транспонирование в NCHW: [1, 3, 448, 448]
    """
    img = Image.open(img_path).convert("RGB")
    
    # 1. Паддинг до квадрата
    w, h = img.size
    max_dim = max(w, h)
    pad_left = (max_dim - w) // 2
    pad_top = (max_dim - h) // 2
    padded = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
    padded.paste(img, (pad_left, pad_top))
    
    # 2. Ресайз
    if max_dim != target_size:
        padded = padded.resize((target_size, target_size), Image.BICUBIC)
    
    # 3. Нормализация [0,1] -> ImageNet
    arr = np.array(padded, dtype=np.float32) / 255.0
    arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
    
    # 4. Транспонирование: HWC -> CHW -> NCHW
    arr = np.transpose(arr, (2, 0, 1))[np.newaxis, :]  # [1, 3, 448, 448]
    return arr

def stage2_tag(masters):
    print("🏷️ Stage 2: AI Tagging (PixAI-Tagger-v0.9)")
    
    model_path = f"/workspace/{MODEL_NAME}"
    if not os.path.exists(model_path):
        print(f"❌ Model {MODEL_NAME} not found!")
        sys.exit(1)
    
    # Загружаем только список тегов (без порогов)
    tags_df = pd.read_csv(f"/workspace/{TAGS_CSV}")
    tags_list = tags_df['name'].astype(str).tolist()
    print(f"✅ Loaded {len(tags_list)} tags")
    
    # 🔥 ФИКСИРОВАННЫЕ ПОРОГИ
    GENERAL_THRESHOLD = 0.35      # для общих тегов (1girl, solo, dress...)
    CHARACTER_THRESHOLD = 0.85    # для имён персонажей (megumin, oguri_cap...)
    
    # Простая эвристика: теги с "_" или "(" — скорее всего, character
    def is_character_tag(tag):
        return '_' in tag or '(' in tag or ')' in tag
    
    # Инициализация ONNX
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    try:
        session = ort.InferenceSession(model_path, providers=providers)
        print(f"✅ Backend: {session.get_providers()[0]}")
    except Exception as e:
        print(f"⚠️ GPU failed: {e}. Falling back to CPU...")
        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    
    # Чёрный список мета-тегов
    BLACKLIST = {
        'general', 'no_humans', 'transparent_background', 'negative_space',
        'rating:general', 'rating:questionable', 'rating:explicit', 'rating:sensitive',
        'copyright', 'artist', 'character', 'meta_tags', 'commentary', 'translated',
        'highres', 'absurdres', 'very_long_hair', 'official_alternate_costume'
    }
    
    tags_db = {}
    
    for i in tqdm(range(0, len(masters), BATCH_SIZE), desc="Tagging"):
        batch_paths = masters[i:i+BATCH_SIZE]
        batch_imgs, valid_paths = [], []
        
        for p in batch_paths:
            try:
                inp = preprocess_pixai(p)
                batch_imgs.append(inp)
                valid_paths.append(p)
            except Exception as e:
                print(f"[WARN] Load fail {Path(p).name}: {e}")
        
        if not batch_imgs: continue
        
        inp = np.concatenate(batch_imgs, axis=0)
        out = session.run(None, {"input": inp})[0]
        
        for idx, p in enumerate(valid_paths):
            probs = out[idx]
            detected = []
            
            for tag_idx, prob in enumerate(probs):
                tag_name = tags_list[tag_idx]
                
                # Пропускаем чёрный список
                if tag_name in BLACKLIST:
                    continue
                
                # Выбираем порог
                threshold = CHARACTER_THRESHOLD if is_character_tag(tag_name) else GENERAL_THRESHOLD
                
                if prob > threshold:
                    detected.append(tag_name)
            
            tags_db[os.path.basename(p)] = detected
    
    with open(TAGS_JSON, 'w') as f: json.dump(tags_db, f, indent=2)
    print(f"✅ Tagged {len(tags_db)} masters → saved to {TAGS_JSON}")
    return tags_db

def main():
    print("🚀 Starting PixAI-Tagger Pipeline...")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    
    files = get_images()
    if not files:
        print("❌ No images found!")
        return
    
    print(f"📊 Found {len(files)} images. Starting pipeline...")
    
    db = stage1_dedup(files)
    tags = stage2_tag(db["masters"])
    
    print("🎉 Tagging complete! Check sorted/tags.json for results.")

if __name__ == "__main__":
    main()
