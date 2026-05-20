import os, json, shutil
from pathlib import Path
from tqdm import tqdm

# ================= КОНФИГУРАЦИЯ =================
SOURCE = Path("/workspace/source")
SORTED = Path("/workspace/sorted")
TAGS_FILE = SORTED / "tags.json"
DRY_RUN = False  # ⚠️ True = только логирование, False = реальное перемещение
# ================================================

# 1. Теги-ловушки (Traps)
TRAP_TAGS = {"otoko_no_ko", "crossdressing", "bulge", "astolfo_(fate)", "bridget_(guilty_gear)", "futanari", "yaoi"}

# 2. NSFW индикаторы
NSFW_TAGS = {"nude", "genitals", "penis", "vagina", "anus", "nipples", "cum", "orgasm", "masturbation", "sex", "nsfw", "r18"}

# 3. Субкатегории
SUBCAT_MAP = {
    "3d": {"realistic"},
    "feet": {"feet", "barefoot", "toes", "foot_focus", "footjob", "foot_sweat"},
    "furry": {"furry", "paw_pads", "fur", "anthro", "feral", "anthropomorphic"},
    "loli": {"loli", "flat_chest", "petite", "no_breasts", "underage", "minor", "juvenile", "youth", "kid", "shota"}
}

# 4. Источники/IP
IP_MAP = {
    "gayshit": {"_(genshin_impact)"},
    "arkn": {"_(arknights)", "arknights"},
    "konosuba": {"megumin", "_(konosuba)", "lalatina", "kazuma", "aqua", "darkness"},
    "vt": {t.strip() for t in ["indie_virtual_youtuber", "hoshimachi_suisei", "shishiro_botan", "gawr_gura", "takanashi_kiara", "ninomae_ina'nis", "watson_amelia", "mori_calliope", "tokoyami_towa", "tsunomaki_watame", "amane_kanata", "himemori_luna", "shiranui_flare", "shirakami_fubuki", "natsuiro_matsuri", "akai_haato", "ookami_mio", "murasaki_shion", "nakiri_ayame", "yuzuki_choco", "oomaru_polka", "momosuzu_nene", "kobo_kanaeru", "irys_(hololive)", "hakos_baelz", "nanashi_mumei", "ceres_fauna", "ouro_kronii", "tsukumo_sana", "vestia_zeta", "pavolia_reine", "moona_hoshinova", "airani_iofifteen", "kureiji_ollie", "cecilia_immersia", "rae_(holoenglish)", "gigi_murin", "nerissa_ravencroft", "shiori_novella", "fuwawa_abyssgard", "mococo_abyssgard"]},
    "nier": {"yorha_no._2_type_b", "kaine_(nier)", "2b", "nier_automata", "nier", "yorha_no._9_type_s", "9s", "a2", "pascal_(nier)", "devola", "popola", "emil_(nier)", "kaine", "nier_replicant", "nier_forma", "halua", "louise", "martha", "margaret"},
    "blue_archive": {"blue_archive", "_(blue_archive)", "plana", "arona", "yuuka", "neru", "karin", "asuna", "momoi", "midori", "azusa", "mari", "hifumi", "serika", "ayane", "noa", "moe", "hanako", "haruna", "hasumi", "ibuki", "kazusa", "chise", "natsu", "utsaha", "himari", "saki", "cherino", "marina", "tomoe", "misaka", "mimori", "tsubaki"},
    "nge": {"souryuu_asuka_langley", "ayanami_rei", "makinami_mari_illustrious", "shikinami_asuka_langley", "ikari_shinji", "katsuragi_misato", "nagisa_kaworu", "soryu_asuka_langley", "evangelion", "eva", "nge", "nerv", "seele", "gendo_ikari", "fuyutsuki_kozo", "kaji_ryoji", "horaki_hikari", "suzuhara_touji", "aida_kensuke", "plug_suit", "entry_plug"}
}

def determine_path(tags):
    tags_set = set(tags)
    
    # 1. Приоритет: Traps (>=2 совпадений)
    if sum(1 for t in tags_set if t in TRAP_TAGS) >= 2:
        return "traps"
        
    # 2. NSFW префикс
    is_nsfw = bool(tags_set & NSFW_TAGS)
    base = "r18" if is_nsfw else ""
    
    # 3. Источник/IP
    source = "other"
    for ip_name, ip_tags in IP_MAP.items():
        if tags_set & ip_tags:
            source = ip_name
            break
            
    # 4. Субкатегории
    subcats = []
    has_3d = bool(tags_set & SUBCAT_MAP["3d"])
    has_furry = bool(tags_set & SUBCAT_MAP["furry"])
    has_loli = bool(tags_set & SUBCAT_MAP["loli"])
    has_feet = bool(tags_set & SUBCAT_MAP["feet"])
    
    if has_3d: subcats.append("3d")
    if has_furry: subcats.append("furry")
    if has_loli: subcats.append("loli")
    
    if has_feet:
        if subcats: subcats[-1] = f"{subcats[-1]}/feet"
        else: subcats.append("feet")
            
    parts = [p for p in [base, source] + subcats if p]
    return os.path.join(*parts)

def main():
    if not TAGS_FILE.exists():
        print("❌ tags.json не найден! Запустите теггер перед сортировкой.")
        return

    with open(TAGS_FILE) as f:
        tags_db = json.load(f)

    stats = {"moved": 0, "skipped": 0, "errors": 0}
    files = list(tags_db.items())
    
    print(f"📦 Найдено {len(files)} файлов. Режим: {'DRY RUN (тест)' if DRY_RUN else 'LIVE (перемещение)'}")
    print("⏳ Начинаю сортировку...\n")

    for filename, tags in tqdm(files, desc="Sorting"):
        src = SOURCE / filename
        if not src.exists():
            stats["skipped"] += 1
            continue

        dest_dir = SORTED / determine_path(tags)
        dest = dest_dir / filename

        if DRY_RUN:
            print(f"📄 {filename} -> {dest.relative_to(SORTED)}")
        else:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest = dest_dir / f"{filename.split('.')[0]}_{stats['moved']}.{filename.split('.')[-1]}"
                shutil.move(str(src), str(dest))
                stats["moved"] += 1
            except Exception as e:
                print(f"❌ Ошибка {filename}: {e}")
                stats["errors"] += 1

    print(f"\n✅ Готово. Перемещено: {stats['moved']} | Пропущено: {stats['skipped']} | Ошибок: {stats['errors']}")
    if DRY_RUN:
        print("💡 Чтобы реально переместить файлы, измените DRY_RUN = False в скрипте и запустите снова.")

if __name__ == "__main__":
    main()
