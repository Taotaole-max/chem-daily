// 站内搜索：纯前端。精选索引（日报 + 热榜）一次加载；全部收录按月分片，用到哪个月才加载哪个月。
(function () {
  "use strict";
  var C = window.CHEM_SEARCH, ROOT = C.root, BASE = ROOT + "search/";

  // 中文常用词 → 英文关键词。全部收录只有英文原标题，靠它让中文也能搜到
  var SYN = {
    "电池": ["batter"], "锂电池": ["lithium", "li-ion"], "锂": ["lithium"], "钠": ["sodium"], "钾": ["potassium"],
    "锌": ["zinc"], "镁": ["magnesium"], "锂硫": ["lithium-sulfur", "li-s"], "固态电解质": ["solid-state electrolyte", "solid electrolyte"],
    "电解质": ["electrolyte"], "电极": ["electrode"], "正极": ["cathode"], "负极": ["anode"], "超级电容器": ["supercapacitor"],
    "燃料电池": ["fuel cell"], "太阳能电池": ["solar cell", "photovoltaic"], "光伏": ["photovoltaic", "solar cell"],
    "钙钛矿": ["perovskite"], "催化": ["catal"], "催化剂": ["catalyst"], "光催化": ["photocatal"], "电催化": ["electrocatal"],
    "单原子": ["single-atom", "single atom"], "酶": ["enzyme"], "不对称": ["asymmetric", "enantioselective"], "手性": ["chiral"],
    "合成": ["synthes"], "全合成": ["total synthesis"], "有机合成": ["organic synthesis"], "偶联": ["coupling"], "自由基": ["radical"],
    "光化学": ["photochem"], "光氧化还原": ["photoredox"], "碳氢活化": ["c–h", "c-h activation"],
    "二氧化碳": ["co2", "carbon dioxide"], "氢气": ["hydrogen", "h2"], "氢": ["hydrogen"], "制氢": ["hydrogen evolution", "hydrogen production"],
    "氨": ["ammonia"], "氮": ["nitrogen"], "氧": ["oxygen"], "水分解": ["water splitting", "water oxidation"], "电解水": ["water electrolysis", "water splitting"],
    "甲烷": ["methane"], "甲醇": ["methanol"], "乙烯": ["ethylene"],
    "金属有机框架": ["metal–organic framework", "metal-organic framework", "mof"], "共价有机框架": ["covalent organic framework", "cof"],
    "分子筛": ["zeolite"], "沸石": ["zeolite"], "石墨烯": ["graphene"], "二维": ["2d", "two-dimensional"], "纳米": ["nano"],
    "量子点": ["quantum dot"], "量子": ["quantum"], "晶体": ["crystal"], "单晶": ["single crystal"], "薄膜": ["thin film"],
    "膜": ["membrane"], "聚合物": ["polymer"], "高分子": ["polymer", "macromolec"], "水凝胶": ["hydrogel"], "凝胶": ["gel"],
    "自组装": ["self-assembl"], "超分子": ["supramolecular"], "液晶": ["liquid crystal"], "塑料": ["plastic"], "微塑料": ["microplastic"],
    "回收": ["recycl", "upcycl"], "降解": ["degrad"], "配体": ["ligand"], "配合物": ["complex"], "金属": ["metal"],
    "稀土": ["rare earth", "lanthan"], "铜": ["copper"], "铁": ["iron"], "镍": ["nickel"], "钴": ["cobalt"], "铂": ["platinum"],
    "钯": ["palladium"], "金": ["gold"], "银": ["silver"], "硅": ["silicon"], "硫": ["sulfur"], "磷": ["phosph"], "氟": ["fluor"], "硼": ["boron"],
    "机器学习": ["machine learning", "machine-learning"], "人工智能": ["artificial intelligence", "ai"], "深度学习": ["deep learning"],
    "大模型": ["large language model", "llm"], "计算": ["comput"], "模拟": ["simulat"], "分子动力学": ["molecular dynamics"],
    "密度泛函": ["dft", "density functional"], "光谱": ["spectroscop"], "质谱": ["mass spectrometr"], "核磁": ["nmr"],
    "电镜": ["electron microscop"], "传感": ["sens"], "传感器": ["sensor"], "检测": ["detect"], "成像": ["imaging"], "荧光": ["fluoresc"],
    "单分子": ["single-molecule", "single molecule"], "蛋白": ["protein"], "蛋白质": ["protein"], "多肽": ["peptide"], "肽": ["peptide"],
    "核酸": ["nucleic acid", "dna", "rna"], "药物": ["drug", "pharmac"], "抗生素": ["antibiotic"], "抗菌": ["antibacterial", "antimicrobial"],
    "癌症": ["cancer", "tumor"], "肿瘤": ["tumor", "tumour", "cancer"], "疫苗": ["vaccine"], "糖": ["glyco", "sugar", "carbohydrate"],
    "环境": ["environment"], "污染": ["pollut", "contamina"], "大气": ["atmospher"], "气溶胶": ["aerosol"], "海洋": ["ocean", "marine"],
    "全氟": ["pfas", "perfluoro"], "碳捕集": ["carbon capture", "co2 capture"], "吸附": ["adsor"], "分离": ["separat"],
    "反应器": ["reactor"], "流动化学": ["flow chemistry", "continuous flow"], "化工": ["chemical engineering", "process"],
    "磁": ["magnet"], "超导": ["superconduct"], "半导体": ["semiconductor"], "光电": ["optoelectron", "photodetect"],
    "发光": ["emission", "luminesc", "light-emitting"], "激光": ["laser"], "热电": ["thermoelectric"], "储能": ["energy storage"]
  };

  var $ = function (id) { return document.getElementById(id); };
  var picked = null, manifest = null, shards = {}, allShown = 50, timer = null;
  var state = { q: "", f: "", k: "", s: "3" };

  function getJSON(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function reEsc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // 每个词 → 一个正则：原词或它的英文同义词。短英文词加单词边界，免得 ai 匹配到 chain
  function compile(q) {
    return q.trim().split(/\s+/).filter(Boolean).map(function (w) {
      var alts = [w].concat(SYN[w] || []);
      var parts = alts.map(function (a) {
        a = a.toLowerCase();
        var e = reEsc(a);
        return /^[a-z0-9]{1,4}$/.test(a) ? "\\b" + e + "\\b" : e;
      });
      return { word: w, re: new RegExp(parts.join("|"), "i"), src: parts.join("|") };
    });
  }
  function mark(text, terms) {
    var s = esc(text);
    if (!terms.length) return s;
    var re = new RegExp("(" + terms.map(function (t) { return t.src; }).join("|") + ")", "gi");
    return s.replace(re, "<mark>$1</mark>");
  }
  var KIND = { news: "新闻", preprint: "预印本" };

  function loadMonths() {
    var months = state.s === "all" ? manifest.months : manifest.months.slice(0, 3);
    return Promise.all(months.map(function (m) {
      if (shards[m]) return shards[m];
      return getJSON(BASE + "all-" + m + ".json").then(function (d) { shards[m] = d; return d; });
    }));
  }

  function run() {
    var terms = compile(state.q), f = state.f, k = state.k;
    syncUrl();
    $("hints").hidden = !!(state.q || f);
    if (!terms.length && !f) {
      $("res-picked").hidden = $("res-all").hidden = true;
      $("status").textContent = "";
      return;
    }
    // 精选
    var hits = picked.filter(function (it) {
      if (f !== "" && String(it.f) !== f) return false;
      if (k && it.k !== k) return false;
      var hay = [it.t, it.e, it.b, it.g, it.s].join(" ");
      return terms.every(function (t) { return t.re.test(hay); });
    }).map(function (it) {
      var sc = terms.reduce(function (a, t) { return a + (t.re.test(it.t + " " + it.e) ? 3 : 1); }, 0);
      return { it: it, sc: sc };
    }).sort(function (a, b) { return b.sc - a.sc || (b.it.d > a.it.d ? 1 : -1); });
    renderPicked(hits.map(function (h) { return h.it; }), terms);

    // 全部收录：选了方向就不搜（原始条目没有分方向）
    if (f !== "" || !terms.length) { $("res-all").hidden = true; return; }
    $("status").textContent = "正在加载全部收录…";
    var seen = {};
    hits.forEach(function (h) { seen[h.it.l] = 1; });
    loadMonths().then(function (list) {
      var out = [];
      list.forEach(function (d) {
        d.items.forEach(function (r) {
          var src = d.sources[r[1]];
          if (k && src[1] !== k) return;
          if (seen[r[3]]) return;
          if (terms.every(function (t) { return t.re.test(r[0] + " " + src[0]); })) out.push([r[0], src[0], src[1], r[2], r[3]]);
        });
      });
      out.sort(function (a, b) { return a[3] < b[3] ? 1 : a[3] > b[3] ? -1 : 0; });
      renderAll(out, terms);
      $("status").textContent = "";
    }).catch(function () { $("status").textContent = "全部收录加载失败，刷新再试试。"; });
  }

  function renderPicked(list, terms) {
    $("res-picked").hidden = false;
    $("n-picked").textContent = list.length + " 条";
    $("list-picked").innerHTML = list.length ? list.slice(0, 100).map(function (it) {
      var fc = it.f >= 0 ? ' style="--fc: var(--f' + it.f + ')"' : "";
      var chip = it.f >= 0 ? '<a class="chip" href="' + ROOT + "fields/" + C.slugs[it.f] + '.html">' + esc(C.fields[it.f]) + "</a>" : "";
      var from = it.x === "日报"
        ? '<a class="from-issue" href="' + ROOT + "issues/" + it.p + '.html">' + it.p + " 期日报</a>"
        : '<span class="from-issue">热榜 · 被引 ' + it.c + "</span>";
      return '<li' + fc + '><div class="meta">' + chip + '<span class="src">' + esc(it.s) + (KIND[it.k] ? " · " + KIND[it.k] : "") +
        " · " + esc(it.d) + "</span>" + from + "</div>" +
        '<h3><a href="' + esc(it.l) + '" target="_blank" rel="noopener">' + mark(it.t, terms) + "</a></h3>" +
        (it.b ? '<p class="blurb">' + mark(it.b, terms) + "</p>" : "") +
        '<p class="en">' + mark(it.e, terms) + "</p></li>";
    }).join("") : '<li class="empty-note">精选里没有。日报每天只挑 10 条，可以看看下面的全部收录。</li>';
  }

  var lastAll = [], lastTerms = [];
  function renderAll(list, terms) {
    lastAll = list; lastTerms = terms;
    $("res-all").hidden = false;
    $("n-all").textContent = list.length + " 条";
    var months = state.s === "all" ? manifest.months.length : Math.min(3, manifest.months.length);
    $("all-note").textContent = "按发表时间排，范围：" + (state.s === "all" ? "全部 " : "最近 ") + months + " 个月，英文原标题。";
    $("list-all").innerHTML = list.length ? list.slice(0, allShown).map(function (r) {
      return '<li><span class="r-date">' + r[3] + '</span><span class="src">' + esc(r[1]) + (KIND[r[2]] ? " · " + KIND[r[2]] : "") +
        '</span><a href="' + esc(r[4]) + '" target="_blank" rel="noopener">' + mark(r[0], terms) + "</a></li>";
    }).join("") : '<li class="empty-note">没搜到。换个说法，或者把时间范围改成“全部时间”。</li>';
    $("more-all").hidden = list.length <= allShown;
  }

  function syncUrl() {
    var p = new URLSearchParams();
    ["q", "f", "k"].forEach(function (key) { if (state[key]) p.set(key, state[key]); });
    if (state.s !== "3") p.set("s", state.s);
    var qs = p.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
  }
  function setField(f) {
    state.f = f;
    Array.prototype.forEach.call($("field-filter").children, function (a) {
      a.classList.toggle("on", a.getAttribute("data-f") === f);
    });
  }

  // 读 URL 里的条件
  var p = new URLSearchParams(location.search);
  state.q = p.get("q") || ""; state.k = p.get("k") || ""; state.s = p.get("s") === "all" ? "all" : "3";
  $("q").value = state.q; $("kind").value = state.k; $("scope").value = state.s;
  setField(p.get("f") || "");

  $("search-form").addEventListener("submit", function (e) { e.preventDefault(); clearTimeout(timer); state.q = $("q").value; ready && run(); });
  $("q").addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { state.q = $("q").value; allShown = 50; ready && run(); }, 250);
  });
  $("kind").addEventListener("change", function () { state.k = this.value; ready && run(); });
  $("scope").addEventListener("change", function () { state.s = this.value; ready && run(); });
  $("field-filter").addEventListener("click", function (e) {
    var a = e.target.closest("a"); if (!a) return;
    e.preventDefault(); setField(a.getAttribute("data-f")); ready && run();
  });
  $("more-all").addEventListener("click", function () { allShown += 50; renderAll(lastAll, lastTerms); });

  var ready = false;
  $("status").textContent = "加载索引…";
  Promise.all([getJSON(BASE + "picked.json"), getJSON(BASE + "manifest.json")]).then(function (r) {
    picked = r[0].items; manifest = r[1]; ready = true;
    $("status").textContent = "";
    run();
  }).catch(function () { $("status").textContent = "索引加载失败，刷新再试试。"; });
})();
