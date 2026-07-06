const state = {
  rows: [],
  query: "",
};

const body = document.querySelector("#dividend-body");
const searchInput = document.querySelector("#search-input");
const reloadButton = document.querySelector("#reload-button");
const rowCount = document.querySelector("#row-count");
const generatedAt = document.querySelector("#generated-at");

const displayColumns = [
  "\u6392\u540d",
  "\u767b\u8bb0\u65e5",
  "\u80a1\u7968",
  "\u6bcf10\u80a1\u6d3e\u606f",
  "\u6700\u65b0\u80a1\u4ef7",
  "\u672c\u6b21\u80a1\u606f\u7387",
];

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];

    if (char === '"' && quoted && next === '"') {
      cell += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      row.push(cell);
      if (row.some((value) => value.trim() !== "")) {
        rows.push(row);
      }
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }

  return rows;
}

function normalizeRows(csvRows) {
  if (csvRows.length <= 1) {
    return [];
  }

  return csvRows.slice(1).map((cells) => {
    const normalized = {};
    displayColumns.forEach((column, index) => {
      normalized[column] = (cells[index] || "").trim();
    });
    return normalized;
  });
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return value || "--";
  }
  return `${numeric.toFixed(4)}%`;
}

function render() {
  const query = state.query.trim().toLowerCase();
  const rows = query
    ? state.rows.filter((row) => row[displayColumns[2]].toLowerCase().includes(query))
    : state.rows;

  rowCount.textContent = `${rows.length} rows`;

  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-state">No rows</td></tr>';
    return;
  }

  body.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${row[displayColumns[0]]}</td>
          <td>${row[displayColumns[1]]}</td>
          <td>${row[displayColumns[2]]}</td>
          <td>${row[displayColumns[3]]}</td>
          <td>${row[displayColumns[4]]}</td>
          <td class="yield">${formatPercent(row[displayColumns[5]])}</td>
        </tr>
      `,
    )
    .join("");
}

async function loadMetadata() {
  try {
    const response = await fetch("metadata.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("metadata missing");
    }
    const metadata = await response.json();
    generatedAt.textContent = metadata.generated_at_label || metadata.generated_at || "Updated";
  } catch {
    generatedAt.textContent = "Metadata unavailable";
  }
}

async function loadData() {
  body.innerHTML = '<tr><td colspan="6" class="empty-state">Loading...</td></tr>';
  try {
    const response = await fetch("dividend_top20.csv", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const csvRows = parseCsv(await response.text());
    state.rows = normalizeRows(csvRows);
    render();
  } catch (error) {
    rowCount.textContent = "-- rows";
    body.innerHTML = `<tr><td colspan="6" class="empty-state error-state">Failed to load data: ${error.message}</td></tr>`;
  }
}

searchInput.addEventListener("input", (event) => {
  state.query = event.target.value;
  render();
});

reloadButton.addEventListener("click", () => {
  loadMetadata();
  loadData();
});

loadMetadata();
loadData();
