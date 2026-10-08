/* CFB Top 25 -- renders the published JSON. No framework, no build step.
 *
 * Two rules worth keeping:
 *   1. Everything is built with createElement/textContent, never innerHTML with
 *      data. Team names contain "&" and apostrophes.
 *   2. The page contains no ranking logic. Every justification string comes from
 *      the JSON, so the site and the engine can never disagree.
 */
(function () {
  "use strict";

  var SVG = "http://www.w3.org/2000/svg";
  var DASH = "\u2013";

  // ---------- tiny DOM helpers ----------
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") n.className = attrs[k];
        else if (k === "text") n.textContent = attrs[k];
        else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
      });
    }
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function svgEl(tag, attrs) {
    var n = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    });
    return n;
  }
  function byId(id) { return document.getElementById(id); }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
  // A wide data table scrolls inside its own box rather than widening the page.
  function scrollable(table) { return el("div", { class: "table-scroll" }, [table]); }
  function num(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return DASH;
    return Number(v).toFixed(digits === undefined ? 0 : digits);
  }
  function signed(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return DASH;
    var s = Number(v).toFixed(digits === undefined ? 1 : digits);
    return Number(v) > 0 ? "+" + s : s;
  }
  function ordinal(n) {
    var tens = n % 100, ones = n % 10;
    var suffix = tens >= 11 && tens <= 13 ? "th"
      : ones === 1 ? "st" : ones === 2 ? "nd" : ones === 3 ? "rd" : "th";
    return n + suffix;
  }

  function driftText(d) {
    if (!d) return "no change from its resume position";
    return (d > 0 ? "up " : "down ") + Math.abs(d) + " from its resume position";
  }

  // ---------- storage (may throw in private mode) ----------
  function readStore(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function writeStore(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* ignore */ } }

  // ---------- theme ----------
  function initTheme() {
    var btn = byId("theme-toggle");
    var saved = readStore("cfb-theme");
    if (saved === "dark" || saved === "light") document.documentElement.setAttribute("data-theme", saved);
    function sync() {
      var dark = document.documentElement.getAttribute("data-theme") === "dark" ||
        (!document.documentElement.getAttribute("data-theme") &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      btn.textContent = dark ? "Light mode" : "Dark mode";
      btn.setAttribute("aria-pressed", dark ? "true" : "false");
    }
    btn.addEventListener("click", function () {
      var dark = document.documentElement.getAttribute("data-theme") === "dark" ||
        (!document.documentElement.getAttribute("data-theme") &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      var next = dark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      writeStore("cfb-theme", next);
      sync();
    });
    sync();
  }

  // ---------- fetch ----------
  function getJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
      return r.json();
    });
  }

  // ---------- header ----------
  function renderMeta(data) {
    var m = data.meta || {};
    var season = m.season || {};
    var parts = [season.label, season.year].filter(Boolean).join(" · ");
    var when = m.generated_at ? " · generated " + m.generated_at.replace("T", " ").replace("Z", " UTC") : "";
    byId("stamp").textContent = parts + when;

    var src = m.source || {};
    var stale = byId("banner-stale");
    if (src.stale) {
      stale.textContent = "The data feed was unreachable on the last run, so these numbers come from a cached copy and may be out of date.";
      stale.hidden = false;
    }
    var sample = byId("banner-sample");
    if (src.synthetic) {
      sample.textContent = "SAMPLE DATA — this snapshot was generated from fixtures, not a live feed.";
      sample.hidden = false;
    }
    byId("attribution").textContent = src.attribution || "";

    var c = m.counts || {};
    var cost = m.cost || {};
    var pills = [
      ["Teams rated", c.rated_teams],
      ["Candidate pool", c.pool_size],
      ["H2H honoured", c.h2h_honored + " of " + (c.h2h_honored + c.h2h_overridden)],
      ["Contradiction loops", c.cycles + (c.largest_cycle > 1 ? " (largest " + c.largest_cycle + ")" : "")],
      ["Upset regressions", c.regressions],
      ["Biggest rise / fall", "+" + c.max_rise + " / −" + c.max_drop]
    ];
    var row = byId("summary");
    clear(row);
    pills.forEach(function (p) {
      if (p[1] === undefined || p[1] === null) return;
      row.appendChild(el("li", { class: "pill" }, [p[0] + " ", el("b", { text: String(p[1]) })]));
    });
  }

  // ---------- rankings table ----------
  function deltaCell(mv) {
    mv = mv || {};
    if (!mv.has_previous) return el("span", { class: "delta same", text: "–" });
    if (mv.status === "new") return el("span", { class: "delta new", text: "NEW" });
    if (mv.status === "up") return el("span", { class: "delta up", text: "▲" + mv.delta });
    if (mv.status === "down") return el("span", { class: "delta down", text: "▼" + Math.abs(mv.delta) });
    return el("span", { class: "delta same", text: "–" });
  }

  var FLAG_TEXT = { cycle: "loop", lifted: "rose", dropped: "fell", regressed: "regressed" };

  function marketText(m) {
    // An em dash rather than a 0: a team with too few lined games has no market
    // rating at all, and showing zero would read as "exactly average".
    return m && m.rating !== null && m.rating !== undefined ? signed(m.rating, 1) : "\u2014";
  }

  function rankText(r) {
    return r && r.rank !== null && r.rank !== undefined ? "#" + r.rank : "\u2014";
  }

  function notesCell(row) {
    var frag = document.createDocumentFragment();
    if (row.cycle) frag.appendChild(el("span", { class: "badge cycle", text: row.cycle }));
    (row.flags || []).forEach(function (f) {
      if (f === "cycle") return;
      frag.appendChild(el("span", { class: "badge " + f, text: FLAG_TEXT[f] || f }));
    });
    return frag;
  }

  // The page-level table names both teams in its first column, so the short
  // winner/loser labels read fine there. Inside a team's own panel the row says
  // "lost to X", so "loser has beaten better since" is ambiguous -- name them.
  var RULE_SENTENCE = {
    1: function (e) { return e.winner + " has more losses"; },
    2: function (e) { return e.winner + " has lost since"; },
    3: function (e) { return e.winner + " has lost more since"; },
    4: function (e) { return e.loser + " has beaten better teams since"; }
  };

  function ruleText(e) {
    var g = e.grounds;
    if (!g || !g.rules || !g.rules.length) return "";
    // Rule 2 is the N=1 case of rule 3, so only the sharper one is worth saying.
    return g.rules
      .filter(function (r) { return !(r === 3 && g.rules.indexOf(2) >= 0); })
      .map(function (r) { return RULE_SENTENCE[r] ? RULE_SENTENCE[r](e) : "rule " + r; })
      .join("; ");
  }

  function h2hTable(row) {
    var rows = [];
    (row.h2h.wins_vs_pool || []).forEach(function (e) { rows.push(["beat", e.loser, e.loser_rank, e, true]); });
    (row.h2h.losses_vs_pool || []).forEach(function (e) { rows.push(["lost to", e.winner, e.winner_rank, e, false]); });
    if (!rows.length) {
      return el("p", { class: "empty", text: "Played nobody else in the candidate pool." });
    }
    rows.sort(function (a, b) { return (a[2] || 99) - (b[2] || 99); });

    var body = el("tbody", null, rows.map(function (r) {
      var verb = r[0], other = r[1], rank = r[2], e = r[3], won = r[4];
      // Score and venue from this team's point of view, not the winner's.
      var score = won ? e.score : e.score.split("-").reverse().join("-");
      var where = e.site === "neutral" ? "neutral" : (won === (e.site === "home") ? "home" : "away");
      return el("tr", null, [
        el("td", { class: "h-verb", text: verb }),
        el("td", { class: "h-opp" }, [el("strong", { text: (rank ? "#" + rank + " " : "") + other })]),
        el("td", { class: "num h-score", "data-label": "Score", text: score }),
        el("td", { class: "h-at", "data-label": "At", text: where }),
        el("td", { class: "num h-adj", "data-label": "Adj", text: signed(e.adj_margin, 1) }),
        el("td", { class: "num h-gap", "data-label": "Rating gap", text: signed(e.rating_gap, 1) }),
        el("td", { class: "h-state" }, [el("span", {
          class: "state " + e.status,
          // An ENFORCED result was honoured because it had to be, which is a
          // different fact from the ranking agreeing with it anyway.
          text: e.status === "overridden" ? "✕ overridden"
            : (e.grounds && e.grounds.enforced ? "✓ required" : "✓ honoured")
        })]),
        el("td", {
          class: "h-why", "data-label": "Permitted by",
          text: e.status === "overridden" ? ruleText(e) : ""
        })
      ]);
    }));

    return el("table", { class: "h2h" }, [
      el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "" }),
        el("th", { scope: "col", text: "Opponent" }),
        el("th", { scope: "col", class: "num", text: "Score" }),
        el("th", { scope: "col", text: "At" }),
        el("th", { scope: "col", class: "num", text: "Adj" }),
        el("th", { scope: "col", class: "num", text: "Rating gap" }),
        el("th", { scope: "col", text: "Status" }),
        el("th", { scope: "col", text: "Permitted by" })
      ])]),
      body
    ]);
  }

  function resumeTable(row) {
    var sched = (row.resume || {}).schedule || [];
    if (!sched.length) {
      return el("p", { class: "empty", text: "No completed games yet." });
    }
    return el("table", { class: "h2h" }, [
      el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "" }),
        el("th", { scope: "col", text: "Opponent" }),
        el("th", { scope: "col", text: "At" }),
        el("th", { scope: "col", class: "num", text: "Wk" }),
        el("th", { scope: "col", class: "num", text: "A top-25 team wins this" })
      ])]),
      el("tbody", null, sched.map(function (g) {
        return el("tr", null, [
          el("td", null, [el("span", {
            class: "state " + (g.won ? "honored" : "overridden"),
            text: g.won ? "W" : "L"
          })]),
          el("td", null, [el("strong", { text: g.opponent })]),
          el("td", { "data-label": "At", text: g.site }),
          el("td", { class: "num", "data-label": "Wk", text: String(g.week) }),
          el("td", { class: "num", "data-label": "Win prob",
                     text: (100 * g.reference_win_probability).toFixed(0) + "%" })
        ]);
      }))
    ]);
  }

  // The four stage-2 components, in a deliberate order with a sentence each.
  // Driven by a table rather than Object.keys(components) so a component without
  // a note still renders and the order never depends on key order.
  var ADJUSTMENTS = [
    {
      key: "best_win", label: "Best win",
      note: function (d) {
        // A NAMED opponent with a credit near zero is a marginal qualifier, not
        // a non-qualifier: the scale starts at the weakest team inside the 25,
        // so beating them is worth almost nothing. That is what stops a cliff
        // at the bar, and it is why the opponent is named either way.
        // The named opponent arrived after some published weeks, and the site
        // still serves those from the week selector. An ABSENT key means the
        // snapshot predates it; a null one means there is genuinely no
        // qualifying win. Saying "no win over a top-25 team" for the first is
        // simply wrong -- that Miami board shows -0.71 for beating Notre Dame.
        if (!("best_win_opponent" in d)) return "";
        if (!d.best_win_opponent) return "no win over a top-25 team";
        return "beat " + d.best_win_opponent + ", rated " +
          signed(d.best_win_opponent_rating, 1);
      }
    },
    {
      key: "cover", label: "Against the number",
      note: function (d) {
        if (d.cover_games === null || d.cover_games === undefined) return "";
        var txt = signed(d.mean_cover_margin, 1) + " per game, covered " +
          d.covers + " of " + d.cover_games;
        // The adjustment is scored off the SHRUNK figure, so the raw mean alone
        // cannot explain the number beside it.
        if (d.shrunk_cover_margin !== null && d.shrunk_cover_margin !== undefined &&
            Math.abs(d.shrunk_cover_margin - d.mean_cover_margin) > 0.05) {
          txt += "; scored as " + signed(d.shrunk_cover_margin, 1) +
            " once shrunk for resting on " + d.cover_games + " lined games";
        }
        if (d.worst_cover) txt += " (worst " + d.worst_cover + ")";
        return txt;
      }
    },
    {
      key: "game_control", label: "Game control",
      note: function (d) {
        return d.game_control_rank ? "#" + d.game_control_rank + " nationally" : "";
      }
    },
    {
      key: "loss_quality", label: "Loss quality",
      note: function (d) {
        if (!d.losses_considered) return "unbeaten \u2014 the best score on this term";
        return d.losses_considered + " loss" + (d.losses_considered === 1 ? "" : "es") +
          ", badness " + num(d.mean_loss_badness, 1) +
          (d.losses_considered === 1 ? "" : " on average") + "; 0 is a perfect loss";
      }
    }
  ];

  // The three base inputs, in weight order with a sentence each. Same shape as
  // ADJUSTMENTS so stages 1 and 2 render through one table builder.
  var BASE_TERMS = [
    {
      key: "SoR", label: "R\u00e9sum\u00e9 (SoR)",
      rank: function (b) { return b.sor_rank; },
      // A pointer rather than a second measurement: the r\u00e9sum\u00e9 section
      // below leads with the probability and then lists every game, so quoting
      // the number here as well would say the same thing twice a few inches
      // apart.
      note: function () {
        return "who this team beat and how hard that was \u2014 the r\u00e9sum\u00e9 below, game by game";
      }
    },
    {
      key: "Mkt", label: "Market (Mkt)",
      rank: function (b) { return b.market_rank; },
      note: function (row) {
        var m = row.market || {};
        if (m.rating === null || m.rating === undefined) return "what the betting market makes this team on a neutral field";
        return signed(m.rating, 1) + " on a neutral field, solved from " +
          m.games + " lined game" + (m.games === 1 ? "" : "s");
      }
    },
    {
      key: "PPA", label: "Play-by-play (PPA)",
      rank: function (b) { return b.ppa_rank; },
      note: function (row) {
        var p = row.performance || {};
        if (p.rating === null || p.rating === undefined) return "points added per play, opponent-adjusted";
        return signed(p.rating, 2) + " points added per play, opponent-adjusted, over " +
          p.games + " game" + (p.games === 1 ? "" : "s");
      }
    }
  ];

  // Category / number / sentence, shared by stages 1 and 2. The sentence is the
  // point of it: the flat label-and-value pairs this replaced left a reader
  // guessing which number belonged to which component.
  function termTable(rows, total) {
    var body = el("tbody", null, rows.map(function (r) {
      return el("tr", null, [
        el("td", { class: "adj-what", text: r.label }),
        el("td", { class: "num adj-pts", text: r.value }),
        el("td", { class: "adj-note", text: r.note || "" })
      ]);
    }));
    if (total) {
      body.appendChild(el("tr", { class: "adj-total" }, [
        el("td", { class: "adj-what", text: total.label }),
        el("td", { class: "num adj-pts", text: total.value }),
        el("td", { class: "adj-note", text: total.note || "" })
      ]));
    }
    return el("table", { class: "adj-table" }, [body]);
  }

  function baseTable(row) {
    var b = row.base || {}, weights = b.weights || {};
    // Driven by base.weights, which carries the weights ACTUALLY applied to
    // this team -- a team missing an input is scored on the terms it has, with
    // the rest rescaled, so a hard-coded 0.55/0.30/0.15 would not add up.
    var rows = BASE_TERMS.filter(function (t) {
      return weights[t.key] !== null && weights[t.key] !== undefined &&
        t.rank(b) !== null && t.rank(b) !== undefined;
    }).map(function (t) {
      return {
        label: t.label,
        value: "#" + t.rank(b),
        note: num(weights[t.key], 2) + " of the base \u00b7 " + t.note(row)
      };
    });
    return termTable(rows, {
      label: "Base", value: num(b.raw_score, 2),
      note: "the ranks above at the weights beside them"
    });
  }

  function adjustmentTable(ra, base) {
    var comp = ra.components || {}, det = ra.detail || {};
    var rows = ADJUSTMENTS.filter(function (a) {
      return comp[a.key] !== null && comp[a.key] !== undefined;
    }).map(function (a) {
      return { label: a.label, value: signed(comp[a.key], 2), note: a.note(det) };
    });
    // Where the score stands once the adjustment is applied. Stage 3 takes it
    // from here, so the panel can be read straight down without arithmetic.
    var after = base.raw_score + ra.total;
    return termTable(rows, {
      label: "Total", value: signed(ra.total, 2),
      note: "Base " + num(base.raw_score, 2) + " \u2192 " + num(after, 2)
    });
  }

  function listJoin(names) {
    if (names.length <= 1) return names.join("");
    return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }

  // Stage 4 only ever argues about results against other teams in the candidate
  // pool, so name them here rather than leaving the reader to infer which rows
  // of the table below this stage was actually looking at.
  function poolText(row) {
    var h = row.h2h || {};
    var beat = (h.wins_vs_pool || []).map(function (e) { return e.loser; });
    var lost = (h.losses_vs_pool || []).map(function (e) { return e.winner; });
    if (!beat.length && !lost.length) {
      return "Played nobody else in the candidate pool, so this stage had nothing to apply.";
    }
    var parts = [];
    if (beat.length) parts.push("beat " + listJoin(beat));
    if (lost.length) parts.push("lost to " + listJoin(lost));
    var head = "Inside the pool: " + parts.join("; ") + ".";
    return head + (row.placement.drift === 0
      ? " None of it changed this placement \u2014 the table below has each result."
      : " The table below says which results were honoured and which were ranked against.");
  }

  function stage(n, title, result, kids) {
    return el("div", { class: "stage" }, [
      el("h4", null, [
        el("span", { class: "stage-n", text: "Stage " + n }),
        el("span", { class: "stage-title", text: title })
      ]),
      el("p", { class: "stage-result", text: result })
    ].concat(kids || []));
  }

  function detailPanel(row) {
    var base = row.base || {};
    var ra = row.resume_adjustment || {};
    var res = row.resume || {};

    // Three positions, each AFTER a further stage. `base.rank` is the position
    // after stages 1, 2 AND 3 -- it is sorted on base_score, which already
    // contains both adjustments. Labelling it as "before" the resume adjustment
    // would let a team show the same number twice next to a large adjustment and
    // look as though nothing had happened.
    var stages = el("div", { class: "stages" }, [
      stage(1, "the three numbers",
        "#" + base.resume_rank + "  \u2014  Base " + num(base.raw_score, 2),
        [baseTable(row)].concat(
          (base.missing || []).map(function (label) {
            return el("p", {
              class: "stage-note",
              text: "No " + label + " this week \u2014 the other terms were rescaled to carry it"
            });
          })
        )),
      stage(2, "r\u00e9sum\u00e9 adjustment",
        signed(ra.total, 2) + " rank points",
        [adjustmentTable(ra, base)]),
      stage(3, "upset regression",
        row.regression
          ? signed(row.regression.adjustment, 2) + " rank points"
          : "none this week",
        (row.regression
          ? (row.regression.notes || []).map(function (n) {
              return el("p", { class: "stage-note", text: n + "." });
            })
          : [el("p", {
              class: "stage-note",
              text: "No result this week spanned a wide enough gap to pull both teams together."
            })])),
      stage(4, "head-to-head",
        "#" + row.rank + "  \u2014  " + driftText(row.placement.drift),
        [
          el("p", {
            class: "stage-note",
            text: "Went in at #" + base.rank + " on " + num(base.score, 2) +
              " after stages 1 to 3."
          }),
          el("p", { class: "stage-note", text: poolText(row) })
        ])
    ]);

    var resumeLine = res.probability === null || res.probability === undefined
      ? ""
      : "#" + res.rank + " \u2014 a top-25 team matches this record " +
        (100 * res.probability).toFixed(1) + "% of the time \u00b7 " +
        res.actual_wins + " wins against " + num(res.expected_wins, 2) + " expected";

    return el("tr", { class: "detail" }, [
      el("td", { colspan: "11" }, [
        el("div", { class: "detail-wide" }, [
          el("h3", { text: "Why " + row.team + " is ranked " + ordinal(row.rank) }),
          stages,

          el("h3", { text: "The r\u00e9sum\u00e9, game by game" }),
          resumeLine ? el("p", { class: "stage-result", text: resumeLine }) : el("span"),
          el("p", { class: "lede", text: "How often a top-25 team would win each of these, and what the market expected. Winning games you were supposed to win does not move the r\u00e9sum\u00e9; the schedule is what makes a record hard to earn." }),
          scrollable(resumeTable(row)),

          el("h3", { text: "Head-to-head inside the pool" }),
          el("p", { class: "lede", text: "\u201cAdj\u201d is the margin once venue is accounted for \u2014 a one-point home win is negative. \u201cRating gap\u201d is how many points apart the market puts the two teams." }),
          scrollable(h2hTable(row))
        ])
      ])
    ]);
  }

  function renderTable(data) {
    var body = byId("rankings-body");
    clear(body);
    (data.rankings || []).forEach(function (row) {
      var detail = detailPanel(row);
      detail.hidden = true;

      var tr = el("tr", {
        class: "row", tabindex: "0", role: "button",
        "aria-expanded": "false",
        "aria-label": "Rank " + row.rank + ", " + row.team + ". Show reasoning."
      }, [
        // Each cell carries its own class so the phone layout can place it in a
        // card grid, and a data-label so a bare number still reads there.
        el("td", { class: "num rank c-rank", text: String(row.rank) }),
        el("td", { class: "c-move" }, [deltaCell(row.movement)]),
        el("td", { class: "team c-team", text: row.team }),
        el("td", { class: "conf col-opt c-conf", text: row.conference || "–" }),
        el("td", { class: "c-rec", text: (row.record && row.record.overall) || "–" }),
        el("td", { class: "num c-sor", "data-label": "SoR", text: num(row.base.sor_rank) }),
        // The market rating is shown in points, not as a rank: "+19.5" says this
        // team would be favoured by four over one at +15.5 on neutral ground,
        // which a rank cannot tell you.
        el("td", { class: "num c-mkt", "data-label": "Mkt", text: marketText(row.market) }),
        el("td", { class: "num c-ppa", "data-label": "PPA", text: rankText(row.performance) }),
        el("td", { class: "num c-base", "data-label": "Base", text: num(row.base.raw_score, 2) }),
        el("td", { class: "c-notes" }, [notesCell(row)]),
        el("td", { class: "chev c-chev", text: "›" })
      ]);

      function toggle() {
        var open = tr.getAttribute("aria-expanded") === "true";
        tr.setAttribute("aria-expanded", open ? "false" : "true");
        detail.hidden = open;
        tr.lastChild.textContent = open ? "›" : "‹";
      }
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
      });

      body.appendChild(tr);
      body.appendChild(detail);
    });
  }

  // ---------- cycle diagram ----------
  var DIAGRAM_MAX = 6;

  function cycleSvg(cycle) {
    var members = cycle.members || [];
    var n = members.length;
    var size = 340, cx = size / 2, cy = size / 2, radius = 118;
    var nodeW = 104, nodeH = 32;

    var svg = svgEl("svg", {
      width: size, height: size, viewBox: "0 0 " + size + " " + size,
      role: "img", "aria-label": "Head-to-head loop among " + members.join(", ")
    });
    var defs = svgEl("defs");
    [["arrow-honored", "edge-honored"], ["arrow-overridden", "edge-overridden"]].forEach(function (p) {
      var marker = svgEl("marker", {
        id: p[0], viewBox: "0 0 10 10", refX: "9", refY: "5",
        markerWidth: "5", markerHeight: "5", orient: "auto-start-reverse"
      });
      var path = svgEl("path", { d: "M 0 1 L 9 5 L 0 9 z" });
      path.setAttribute("class", p[1] === "edge-honored" ? "node-arrow-honored" : "node-arrow-overridden");
      path.style.fill = p[1] === "edge-honored" ? "var(--ink-2)" : "var(--critical)";
      marker.appendChild(path);
      defs.appendChild(marker);
    });
    svg.appendChild(defs);

    var pos = {};
    members.forEach(function (team, i) {
      var angle = (-Math.PI / 2) + (i * 2 * Math.PI / n);
      pos[team] = { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
    });

    (cycle.edges || []).forEach(function (e) {
      var a = pos[e.winner], b = pos[e.loser];
      if (!a || !b) return;
      var dx = b.x - a.x, dy = b.y - a.y;
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      var inset = 26;
      var line = svgEl("line", {
        x1: a.x + (dx / len) * inset, y1: a.y + (dy / len) * inset,
        x2: b.x - (dx / len) * inset, y2: b.y - (dy / len) * inset,
        "marker-end": "url(#arrow-" + e.status + ")"
      });
      line.setAttribute("class", "edge-" + e.status);
      var title = svgEl("title");
      title.textContent = e.winner + " beat " + e.loser + " " + e.score +
        " (" + e.site + ") — " + (e.status === "overridden" ? "set aside" : "honoured");
      line.appendChild(title);
      svg.appendChild(line);
    });

    members.forEach(function (team, i) {
      var p = pos[team];
      var g = svgEl("g");
      var rect = svgEl("rect", {
        x: p.x - nodeW / 2, y: p.y - nodeH / 2, width: nodeW, height: nodeH, rx: 7
      });
      rect.setAttribute("class", "node-shape");
      var short = team.length > 14 ? team.slice(0, 13) + "…" : team;
      var label = svgEl("text", { x: p.x, y: p.y + 1, "text-anchor": "middle" });
      label.setAttribute("class", "node-label");
      label.textContent = short;
      var rank = svgEl("text", { x: p.x, y: p.y + 12, "text-anchor": "middle" });
      rank.setAttribute("class", "node-rank");
      rank.textContent = cycle.member_ranks && cycle.member_ranks[i] ? "#" + cycle.member_ranks[i] : "";
      var title = svgEl("title");
      title.textContent = team;
      g.appendChild(rect); g.appendChild(label); g.appendChild(rank); g.appendChild(title);
      svg.appendChild(g);
    });

    return svg;
  }

  function edgeList(edges, caption) {
    if (!edges.length) return el("p", { class: "empty", text: "None." });
    return el("table", { class: "h2h" }, [
      el("caption", { text: caption }),
      el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "Result" }),
        el("th", { scope: "col", class: "num", text: "Score" }),
        el("th", { scope: "col", text: "At" }),
        el("th", { scope: "col", class: "num", text: "Adj" }),
        el("th", { scope: "col", class: "num", text: "Rating gap" }),
        el("th", { scope: "col", class: "num", text: "Weight" }),
        el("th", { scope: "col", text: "Status" })
      ])]),
      el("tbody", null, edges.map(function (e) {
        return el("tr", null, [
          el("td", null, [
            el("strong", { text: (e.winner_rank ? "#" + e.winner_rank + " " : "") + e.winner }),
            " beat ",
            el("strong", { text: (e.loser_rank ? "#" + e.loser_rank + " " : "") + e.loser })
          ]),
          el("td", { class: "num", text: e.score }),
          el("td", { text: e.site }),
          el("td", { class: "num", text: signed(e.adj_margin, 1) }),
          el("td", { class: "num", text: signed(e.rating_gap, 1) }),
          el("td", { class: "num", text: num(e.weight, 2) }),
          el("td", null, [el("span", {
            class: "state " + e.status,
            text: e.status === "overridden" ? "✕ set aside" : "✓ honoured"
          })])
        ]);
      }))
    ]);
  }

  function renderCycles(data) {
    var host = byId("cycles");
    clear(host);
    var cycles = data.cycles || [];
    if (!cycles.length) {
      host.appendChild(el("div", { class: "card" }, [
        el("p", { class: "empty", text: "No contradictions this week — every head-to-head result among the ranked teams can be satisfied at once." })
      ]));
      return;
    }

    cycles.forEach(function (c) {
      var kids = [
        el("h3", { text: c.cycle_id + " — " + c.size + " teams, " + (c.edges || []).length + " results" }),
        el("p", { class: "lede", text: c.explanation })
      ];

      if (c.size <= DIAGRAM_MAX) {
        kids.push(el("figure", { class: "cycle-figure" }, [
          cycleSvg(c),
          el("div", null, [
            scrollable(edgeList(c.edges || [], "Every result inside " + c.cycle_id + ".")),
            el("ul", { class: "legend" }, [
              el("li", null, [el("span", { class: "swatch honored" }), "honoured"]),
              el("li", null, [el("span", { class: "swatch overridden" }), "set aside"])
            ])
          ])
        ]));
      } else {
        // A 20-team tangle on a circle is unreadable; the table is the honest view.
        kids.push(el("p", { class: "lede", text:
          "This loop is too large to draw usefully. " + c.size +
          " teams are mutually entangled, which is normal late in a season: " +
          "the results below are the ones the ranking had to set aside." }));
        kids.push(scrollable(edgeList(
          (c.edges || []).filter(function (e) { return e.status === "overridden"; }),
          "Results set aside inside " + c.cycle_id + "."
        )));
        kids.push(el("p", { class: "lede", text: "Members, in ranked order: " + (c.members || []).join(", ") + "." }));
      }

      host.appendChild(el("div", { class: "card" }, [el("div", { class: "card-body" }, kids)]));
    });
  }

  // ---------- "How the ranking works" ----------
  function wireDialog() {
    var dialog = byId("how-it-works");
    var open = byId("how-open");
    var close = byId("how-close");
    if (!dialog || !open) return;
    // Safari only learned showModal() in 15.4. Without it the button would do
    // nothing at all, so send those visitors to the long version instead.
    if (typeof dialog.showModal !== "function") {
      open.addEventListener("click", function () { location.href = "methodology.html"; });
      return;
    }
    open.addEventListener("click", function () {
      dialog.showModal();
      dialog.querySelector(".dialog-body").scrollTop = 0;
    });
    if (close) close.addEventListener("click", function () { dialog.close(); });
    // A click that lands on the dialog element itself is a click on the
    // backdrop -- anything inside it targets a child.
    dialog.addEventListener("click", function (e) {
      if (e.target === dialog) dialog.close();
    });
  }

  // ---------- the four rules ----------
  // A result stands unless one of four things has happened since the game. Which
  // ones apply, and how far apart the two may then sit, are published per result
  // as `grounds`, so this needs no ranking rules of its own.
  var RULE_LABEL = {
    1: "winner has more losses",
    2: "winner has lost since",
    3: "winner has lost more since",
    4: "loser has beaten better since"
  };

  function rulesBadge(e) {
    var g = e.grounds;
    if (!g) return [el("span", { class: "muted", text: "\u2014" })];
    if (!g.rules || !g.rules.length) {
      return [el("span", { class: "state rule-none", text: "no exception" })];
    }
    // Rule 2 is the N=1 case of rule 3, so only the sharper label is shown.
    var shown = g.rules.filter(function (r) { return !(r === 3 && g.rules.indexOf(2) >= 0); });
    return [el("span", {
      class: "state rule-" + shown[shown.length - 1],
      text: shown.map(function (r) { return RULE_LABEL[r] || ("rule " + r); }).join(", ")
    })];
  }

  function leadLabel(e) {
    var g = e.grounds;
    if (!g || e.lead === null || e.lead === undefined) return "\u2014";
    // max_lead is null when the exception allows any gap at all.
    return Math.abs(e.lead) + " / " + (g.max_lead === null ? "any" : num(g.max_lead, 0));
  }

  // ---------- overridden, regressions, tail ----------
  function renderOverridden(data) {
    var host = byId("overridden-list");
    clear(host);
    var rows = data.overridden_results || [];
    if (!rows.length) {
      host.appendChild(el("p", { class: "empty", text: "None — the ranking honours every head-to-head result in the pool." }));
      return;
    }
    host.appendChild(scrollable(el("table", { class: "h2h" }, [
      el("thead", null, [el("tr", null, [
        el("th", { scope: "col", text: "Result" }),
        el("th", { scope: "col", class: "num", text: "Score" }),
        el("th", { scope: "col", text: "At" }),
        el("th", { scope: "col", class: "num", text: "Weight" }),
        el("th", { scope: "col", text: "What permits it" }),
        el("th", { scope: "col", class: "num", text: "Places / allowed" }),
        el("th", { scope: "col", text: "Why it was set aside" })
      ])]),
      el("tbody", null, rows.map(function (e) {
        return el("tr", null, [
          el("td", null, [
            el("strong", { text: (e.winner_rank ? "#" + e.winner_rank + " " : "") + e.winner }),
            " beat ",
            el("strong", { text: (e.loser_rank ? "#" + e.loser_rank + " " : "") + e.loser })
          ]),
          el("td", { class: "num", text: e.score }),
          el("td", { text: e.site }),
          el("td", { class: "num", text: num(e.weight, 2) }),
          el("td", null, rulesBadge(e)),
          el("td", { class: "num", text: leadLabel(e) }),
          el("td", { text: e.reason || "" })
        ]);
      }))
    ])));
  }

  function renderRegressions(data) {
    var host = byId("regression-list");
    clear(host);
    var rows = data.regressions || [];
    if (!rows.length) {
      host.appendChild(el("p", { class: "empty", text: "No result this week spanned a wide enough gap to trigger a regression." }));
      return;
    }
    host.appendChild(el("ul", { class: "reasons" }, rows.map(function (r) {
      return el("li", { text: r.description + " (week " + r.week + ")." });
    })));
  }

  function renderTail(data) {
    var host = byId("tail-list");
    clear(host);
    var rows = data.pool_tail || [];
    if (!rows.length) {
      host.appendChild(el("p", { class: "empty", text: "No further teams in the pool." }));
      return;
    }
    host.appendChild(scrollable(el("table", { class: "h2h" }, [
      el("thead", null, [el("tr", null, [
        el("th", { scope: "col", class: "num", text: "#" }),
        el("th", { scope: "col", text: "Team" }),
        el("th", { scope: "col", text: "Conf" }),
        el("th", { scope: "col", text: "Rec" }),
        el("th", { scope: "col", class: "num", text: "SoR" }),
        el("th", { scope: "col", class: "num", text: "Mkt" }),
        el("th", { scope: "col", class: "num", text: "PPA" })
      ])]),
      el("tbody", null, rows.map(function (r) {
        return el("tr", null, [
          el("td", { class: "num", text: String(r.rank) }),
          el("td", null, [el("strong", { text: r.team })]),
          el("td", { class: "conf", text: r.conference || "–" }),
          el("td", { text: r.record || "–" }),
          el("td", { class: "num", text: num(r.sor_rank) }),
          el("td", { class: "num", text: marketText({ rating: r.market_rating }) }),
          el("td", { class: "num", text: rankText({ rank: r.ppa_rank }) })
        ]);
      }))
    ])));
  }

  // ---------- week selector ----------
  function renderSelector(index, currentId) {
    var sel = byId("week-select");
    clear(sel);
    (index.seasons || []).forEach(function (season) {
      var group = el("optgroup", { label: String(season.year) });
      (season.snapshots || []).slice().reverse().forEach(function (s) {
        // Spell the season out, so an archived 2025 entry can never be mistaken
        // for the current ranking.
        var text = season.year + " · " + (s.label || s.id) + (s.top1 ? " — " + s.top1 : "");
        var o = el("option", { value: s.id, text: text });
        if (s.id === currentId) o.setAttribute("selected", "selected");
        group.appendChild(o);
      });
      sel.appendChild(group);
    });
    sel.addEventListener("change", function () {
      var url = new URL(window.location.href);
      url.searchParams.set("week", sel.value);
      window.location.href = url.toString();
    });
  }

  function findSnapshot(index, id) {
    var all = [];
    (index.seasons || []).forEach(function (s) { all = all.concat(s.snapshots || []); });
    return all.filter(function (s) { return s.id === id; })[0] || null;
  }

  function fail(message) {
    var b = byId("banner-error");
    b.textContent = message;
    b.hidden = false;
  }

  // ---------- boot ----------
  function render(data) {
    renderMeta(data);
    renderTable(data);
    renderCycles(data);
    renderOverridden(data);
    renderRegressions(data);
    renderTail(data);
  }

  function boot() {
    initTheme();
    wireDialog();
    var wanted = new URLSearchParams(window.location.search).get("week");

    // The Pages CDN caches aggressively: bust the catalogue by time so a new
    // week shows up immediately, and each snapshot by its content hash so it
    // can be cached forever yet never go stale.
    getJSON("data/index.json?t=" + Date.now())
      .then(function (index) {
        var id = wanted || index.current;
        var entry = findSnapshot(index, id);
        renderSelector(index, id);
        var path = entry && entry.path ? entry.path : "rankings.json";
        var bust = entry && entry.content_hash ? "?v=" + encodeURIComponent(entry.content_hash) : "";
        return getJSON("data/" + path + bust);
      })
      .catch(function () {
        // No catalogue yet (or an unknown week): the current snapshot still works.
        return getJSON("data/rankings.json?t=" + Date.now());
      })
      .then(render)
      .catch(function (err) {
        fail("Could not load the rankings data. " + (err && err.message ? err.message : ""));
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
