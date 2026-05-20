from flask import Flask, render_template_string, send_from_directory, request, jsonify
import json
import os
from pathlib import Path
from PIL import Image
import base64
from io import BytesIO

app = Flask(__name__)

# ================= КОНФИГУРАЦИЯ =================
WORKSPACE = Path("/workspace")
SOURCE = WORKSPACE / "source"
SORTED = WORKSPACE / "sorted"
TAGS_FILE = SORTED / "tags.json"

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

ROOT_CATEGORIES = ["traps", "r18", "gayshit", "arkn", "konosuba", "vt", "nier", "blue_archive", "nge", "other"]

def determine_category_path(tags):
    tags_set = set(tags)
    
    # 1. Traps (приоритет)
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
    return "/".join(parts)

# ================= HTML ШАБЛОН =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>🏷️ PixAI Tag Viewer</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #0f111a; color: #e2e8f0; padding: 20px; }
        .header { background: #1a1d2e; padding: 15px 20px; border-radius: 8px; margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center; }
        .stats { display: flex; gap: 15px; font-size: 14px; color: #94a3b8; }
        .stats b { color: #38bdf8; }
        .controls { background: #1a1d2e; padding: 15px; border-radius: 8px; margin-bottom: 15px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        input, select, button { padding: 8px 12px; border: 1px solid #334155; border-radius: 6px; background: #0f111a; color: #e2e8f0; font-size: 14px; }
        input { min-width: 220px; }
        button { background: #2563eb; border: none; cursor: pointer; font-weight: 500; }
        button:hover { background: #1d4ed8; }
        table { width: 100%; border-collapse: collapse; background: #1a1d2e; border-radius: 8px; overflow: hidden; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #334155; }
        th { background: #252b3d; cursor: pointer; user-select: none; }
        tr:hover { background: #252b3d; }
        .thumb { width: 70px; height: 70px; object-fit: cover; border-radius: 6px; cursor: pointer; transition: transform 0.2s; }
        .thumb:hover { transform: scale(1.15); }
        .tags { display: flex; flex-wrap: wrap; gap: 4px; max-width: 400px; }
        .tag { background: #334155; color: #cbd5e1; padding: 2px 6px; border-radius: 4px; font-size: 11px; }
        .path { font-family: monospace; font-size: 12px; color: #94a3b8; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .cat-path { font-size: 12px; font-weight: 600; color: #38bdf8; }
        .modal { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background: rgba(0,0,0,0.9); z-index: 1000; justify-content: center; align-items: center; }
        .modal img { max-width: 95%; max-height: 95%; border-radius: 8px; }
        .close { position: absolute; top: 20px; right: 30px; font-size: 36px; color: white; cursor: pointer; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🏷️ PixAI Tag Viewer</h1>
        <div class="stats">
            <div>Всего: <b>{{ total }}</b></div>
            <div>Показано: <b>{{ shown }}</b></div>
        </div>
    </div>
    
    <div class="controls">
        <input type="text" id="search" placeholder="🔍 Поиск по тегам или имени..." oninput="filterTable()">
        <select id="catFilter" onchange="filterTable()">
            <option value="">Все категории</option>
            {% for cat in root_cats %}
            <option value="{{ cat }}">{{ cat }}</option>
            {% endfor %}
        </select>
        <input type="number" id="perPage" value="100" min="20" max="500" onchange="location.reload()">
        <span style="color:#64748b; font-size:13px;">на стр.</span>
        <div style="flex:1"></div>
        <button onclick="saveChanges()">💾 Сохранить</button>
        <button onclick="exportCSV()">📥 CSV</button>
    </div>
    
    <table>
        <thead>
            <tr>
                <th>Превью</th>
                <th>Теги</th>
                <th>Категория (путь)</th>
                <th>Файл</th>
            </tr>
        </thead>
        <tbody id="tbody">
            {% for item in items %}
            <tr data-path="{{ item.path }}" data-cat="{{ item.category }}">
                <td><img src="/thumb/{{ item.filename }}" class="thumb" onclick="openModal('{{ item.filename }}')"></td>
                <td><div class="tags">{% for t in item.tags %}<span class="tag">{{ t }}</span>{% endfor %}</div></td>
                <td><span class="cat-path">{{ item.category }}</span></td>
                <td class="path" title="{{ item.path }}">{{ item.path }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    
    <div class="modal" id="modal" onclick="this.style.display='none'">
        <span class="close">&times;</span>
        <img id="modalImg">
    </div>

    <script>
        let changes = {};
        function filterTable() {
            const q = document.getElementById('search').value.toLowerCase();
            const cat = document.getElementById('catFilter').value;
            const rows = document.querySelectorAll('#tbody tr');
            let shown = 0;
            rows.forEach(r => {
                const text = r.textContent.toLowerCase();
                const matchQ = !q || text.includes(q);
                const matchC = !cat || r.dataset.cat.startsWith(cat);
                r.style.display = (matchQ && matchC) ? '' : 'none';
                if(matchQ && matchC) shown++;
            });
            document.querySelector('.stats b:nth-child(2)').textContent = shown;
        }
        function openModal(f) {
            document.getElementById('modalImg').src = '/image/' + f;
            document.getElementById('modal').style.display = 'flex';
        }
        function saveChanges() {
            fetch('/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(changes)})
            .then(r=>r.json()).then(d => { alert('✅ Сохранено: ' + d.saved + ' файлов'); changes={}; });
        }
        function exportCSV() {
            let csv = 'Filename,Category,Tags,Path\\n';
            document.querySelectorAll('#tbody tr').forEach(r => {
                csv += `"${r.dataset.path.split('/').pop()}","${r.dataset.cat}","${[...r.querySelectorAll('.tag')].map(t=>t.textContent).join(';')}","${r.dataset.path}"\\n`;
            });
            const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv])); a.download = 'tags.csv'; a.click();
        }
        document.addEventListener('keydown', e => { if(e.key==='Escape') document.getElementById('modal').style.display='none'; });
    </script>
</body>
</html>
"""

# ================= РОУТЫ =================
@app.route('/')
def index():
    if not TAGS_FILE.exists():
        return "<h1>❌ tags.json не найден</h1><p>Сначала запустите run_pipeline.py</p>"
    
    with open(TAGS_FILE) as f:
        tags_db = json.load(f)
    
    items = []
    for fname, tags in tags_db.items():
        cat = determine_category_path(tags)
        path = f"source/{fname}"
        for c in ["traps"] + ROOT_CATEGORIES:
            if (SORTED / c / fname).exists():
                path = f"sorted/{c}/{fname}"
                break
        items.append({"filename": fname, "path": path, "tags": tags, "category": cat})
    
    per_page = int(request.args.get('perPage', 100))
    total = len(items)
    page_items = items[:per_page]
    
    return render_template_string(HTML_TEMPLATE, items=page_items, total=total, shown=len(page_items), root_cats=ROOT_CATEGORIES)

@app.route('/thumb/<filename>')
def thumb(filename):
    for base in [SOURCE, SORTED]:
        for sub in [""] + ROOT_CATEGORIES + ["r18", "3d", "feet", "furry", "loli", "other"]:
            p = base / sub / filename if sub else base / filename
            if p.exists():
                return send_from_directory(p.parent, p.name)
    return "Not found", 404

@app.route('/image/<filename>')
def full_image(filename):
    return thumb(filename)

@app.route('/save', methods=['POST'])
def save_changes():
    mods = request.json
    with open(TAGS_FILE) as f: db = json.load(f)
    saved = 0
    for fname, m in mods.items():
        if fname in db:
            for t in m.get('added', []): 
                if t not in db[fname]: db[fname].append(t)
            for t in m.get('removed', []): 
                if t in db[fname]: db[fname].remove(t)
            saved += 1
    with open(TAGS_FILE, 'w') as f: json.dump(db, f, indent=2)
    return jsonify({"saved": saved})

if __name__ == '__main__':
    print("🌐 Запуск: http://localhost:5001")
    app.run(host='0.0.0.0', port=5000)
