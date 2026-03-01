const chat = document.getElementById("chat");
const form = document.getElementById("askForm");
const input = document.getElementById("question");

function bubble(text, cls) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

function renderTable(parent, table) {
  if (!table || !table.rows || !table.rows.length) return;
  const wrap = document.createElement("div");
  wrap.className = "viz";
  const t = document.createElement("table");
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  table.columns.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    trh.appendChild(th);
  });
  thead.appendChild(trh);
  t.appendChild(thead);

  const tbody = document.createElement("tbody");
  table.rows.forEach((r) => {
    const tr = document.createElement("tr");
    table.columns.forEach((c) => {
      const td = document.createElement("td");
      td.textContent = String(r[c] ?? "");
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);
  parent.appendChild(wrap);
}

function renderChart(parent, chartData) {
  if (!chartData || !chartData.labels || !chartData.datasets) return;
  const wrap = document.createElement("div");
  wrap.className = "viz";
  const title = document.createElement("div");
  title.className = "chartTitle";
  title.textContent = chartData.title || "Insight";
  wrap.appendChild(title);

  const dataset = chartData.datasets[0] || { data: [] };
  const values = dataset.data || [];
  const labels = chartData.labels || [];
  const max = Math.max(...values, 1);

  const bars = document.createElement("div");
  bars.className = chartData.type === "pie" ? "pieList" : "barList";

  for (let i = 0; i < labels.length; i += 1) {
    const row = document.createElement("div");
    row.className = "barRow";

    const l = document.createElement("div");
    l.className = "barLabel";
    l.textContent = String(labels[i]);

    const track = document.createElement("div");
    track.className = "barTrack";

    const bar = document.createElement("div");
    bar.className = "barFill";
    bar.style.width = `${(Number(values[i]) / max) * 100}%`;

    const v = document.createElement("div");
    v.className = "barValue";
    v.textContent = String(values[i]);

    track.appendChild(bar);
    row.appendChild(l);
    row.appendChild(track);
    row.appendChild(v);
    bars.appendChild(row);
  }

  wrap.appendChild(bars);
  parent.appendChild(wrap);
}

async function ask(question) {
  bubble(question, "user");
  const holder = bubble("Analyzing data...", "bot");
  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    holder.textContent = data.answer_text || data.error || "No response";
    if (data.explanation) {
      const m = document.createElement("div");
      m.className = "meta";
      m.textContent = data.explanation;
      holder.appendChild(m);
    }
    renderChart(holder, data.chart);
    renderTable(holder, data.table);
  } catch (e) {
    holder.textContent = `Error: ${e.message}`;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  ask(q);
});

document.querySelectorAll(".q").forEach((btn) => {
  btn.addEventListener("click", () => {
    ask(btn.textContent.trim());
  });
});

bubble("Ask any question about your UPI transaction CSV.", "bot");
