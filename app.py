import csv
import json
import math
import re
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).parent
CSV_PATH = BASE_DIR / "upi_transactions_2024.csv"
WEB_DIR = BASE_DIR / "web"


def _num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


class DataEngine:
    def __init__(self, csv_path: Path):
        self.rows = []
        self.value_sets = defaultdict(set)
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            self.columns = reader.fieldnames or []
            for r in reader:
                row = {k: (v or "").strip() for k, v in r.items()}
                row["amount_value"] = _num(row.get("amount (INR)", "0"), 0.0)
                row["hour_value"] = int(_num(row.get("hour_of_day", "0"), 0))
                row["weekend_value"] = int(_num(row.get("is_weekend", "0"), 0))
                row["fraud_value"] = int(_num(row.get("fraud_flag", "0"), 0))
                row["failed_value"] = 1 if row.get("transaction_status", "").upper() == "FAILED" else 0
                self.rows.append(row)
                for k, v in row.items():
                    if k.endswith("_value"):
                        continue
                    if v:
                        self.value_sets[k].add(v)
        self.total = len(self.rows)

    def known_values(self):
        return {k: sorted(v) for k, v in self.value_sets.items()}


class QueryEngine:
    DIMENSION_ALIASES = {
        "transaction type": "transaction type",
        "type": "transaction type",
        "merchant category": "merchant_category",
        "category": "merchant_category",
        "device": "device_type",
        "device type": "device_type",
        "network": "network_type",
        "network type": "network_type",
        "sender age group": "sender_age_group",
        "age group": "sender_age_group",
        "receiver age group": "receiver_age_group",
        "sender state": "sender_state",
        "state": "sender_state",
        "sender bank": "sender_bank",
        "receiver bank": "receiver_bank",
        "bank": "sender_bank",
        "day of week": "day_of_week",
        "day": "day_of_week",
        "hour of day": "hour_of_day",
        "hour": "hour_of_day",
        "status": "transaction_status",
    }
    DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    FIELD_LABELS = {
        "transaction type": "transaction type",
        "merchant_category": "merchant category",
        "device_type": "device type",
        "network_type": "network type",
        "sender_age_group": "sender age group",
        "receiver_age_group": "receiver age group",
        "sender_state": "sender state",
        "sender_bank": "sender bank",
        "receiver_bank": "receiver bank",
        "day_of_week": "day of week",
        "hour_of_day": "hour of day",
        "transaction_status": "transaction status",
    }

    def __init__(self, data: DataEngine):
        self.data = data
        self.context = {}

    def ask(self, question: str):
        q = question.strip()
        ql = q.lower()
        filters = self._extract_filters(ql)
        metric = self._detect_metric(ql)
        dimension = self._detect_dimension(ql)
        top_n, ascending = self._detect_rank(ql)

        if not dimension and self._is_followup(ql):
            dimension = self.context.get("dimension")
        if not metric and self._is_followup(ql):
            metric = self.context.get("metric", "count")
        if not metric:
            metric = "count"

        if not dimension and self._should_use_time_series(ql):
            dimension = "hour_of_day"
        if not dimension and self._needs_grouping(ql):
            dimension = self._best_dimension_from_filters(filters)

        if not dimension and self._prefer_single_value(ql, metric):
            answer = self._single_metric_answer(filters, metric, q)
        elif metric == "failure_rate":
            result = self._aggregate_rate(filters, dimension or "transaction type", "failed_value")
            answer = self._ranked_answer(result, "Failure rate", "%", q, filters, dimension or "transaction type", top_n, ascending)
        elif metric == "success_rate":
            result = self._aggregate_rate(filters, dimension or "transaction type", "success")
            answer = self._ranked_answer(result, "Success rate", "%", q, filters, dimension or "transaction type", top_n, ascending)
        elif metric == "flagged_rate":
            if not dimension:
                answer = self._single_rate_answer(filters, "fraud_value", "Flagged rate", q)
            else:
                result = self._aggregate_rate(filters, dimension, "fraud_value")
                answer = self._ranked_answer(result, "Flagged rate", "%", q, filters, dimension, top_n, ascending)
        elif metric == "avg_amount":
            result = self._aggregate_avg(filters, dimension or "transaction type")
            answer = self._ranked_answer(result, "Average amount", "INR", q, filters, dimension or "transaction type", top_n, ascending)
        elif metric == "sum_amount":
            result = self._aggregate_sum(filters, dimension or "transaction type")
            answer = self._ranked_answer(result, "Total amount", "INR", q, filters, dimension or "transaction type", top_n, ascending)
        elif metric == "min_amount":
            result = self._aggregate_minmax(filters, dimension or "transaction type", mode="min")
            answer = self._ranked_answer(result, "Minimum amount", "INR", q, filters, dimension or "transaction type", top_n, True)
        elif metric == "max_amount":
            result = self._aggregate_minmax(filters, dimension or "transaction type", mode="max")
            answer = self._ranked_answer(result, "Maximum amount", "INR", q, filters, dimension or "transaction type", top_n, False)
        else:
            result = self._aggregate_count(filters, dimension or "transaction type")
            answer = self._ranked_answer(result, "Transactions", "", q, filters, dimension or "transaction type", top_n, ascending)

        self.context["dimension"] = answer.get("dimension") or dimension
        self.context["metric"] = answer.get("metric") or metric
        self.context["filters"] = filters
        return answer

    def _detect_metric(self, ql: str):
        if "failure rate" in ql or ("failed" in ql and "rate" in ql):
            return "failure_rate"
        if "success rate" in ql or ("relationship" in ql and "success" in ql):
            return "success_rate"
        if "average" in ql and ("amount" in ql or "inr" in ql):
            return "avg_amount"
        if ("sum" in ql or "total" in ql) and ("amount" in ql or "inr" in ql):
            return "sum_amount"
        if ("minimum" in ql or "lowest" in ql or "smallest" in ql) and ("amount" in ql or "inr" in ql):
            return "min_amount"
        if ("maximum" in ql or "highest" in ql or "largest" in ql) and ("amount" in ql or "inr" in ql):
            return "max_amount"
        if "flagged" in ql or "fraud" in ql:
            return "flagged_rate"
        return "count"

    def _detect_dimension(self, ql: str):
        m = re.search(r"\b(?:by|across|per|for each)\s+([a-z_ ]+)", ql)
        if m:
            target = m.group(1).strip()
            col = self._alias_to_column(target)
            if col:
                return col
        m2 = re.search(r"relationship between\s+([a-z_ ]+)\s+and\s+([a-z_ ]+)", ql)
        if m2:
            col = self._alias_to_column(m2.group(1).strip())
            if col:
                return col
        best = None
        for k, v in self.DIMENSION_ALIASES.items():
            if k in ql:
                if best is None or len(k) > len(best[0]):
                    best = (k, v)
        return best[1] if best else None

    def _extract_filters(self, ql: str):
        filters = defaultdict(set)
        for col, vals in self.data.known_values().items():
            for v in vals:
                vlow = v.lower()
                if v and re.search(r"\b" + re.escape(vlow) + r"\b", ql):
                    filters[col].add(v)
        if ("failed transaction" in ql or "failed transactions" in ql or "in failed" in ql) and "failure rate" not in ql:
            filters["transaction_status"].add("FAILED")
        if ("successful transaction" in ql or "successful transactions" in ql or "in success" in ql) and "success rate" not in ql:
            filters["transaction_status"].add("SUCCESS")
        if "weekend" in ql:
            filters["is_weekend"] = {"1"}
        if "weekday" in ql:
            filters["is_weekend"] = {"0"}
        for d in self.DAYS:
            if re.search(r"\b" + d + r"\b", ql):
                filters["day_of_week"].add(d.capitalize())
        # Amount filters
        amt_between = re.search(r"(?:amount|inr)?\s*between\s+(\d+)\s+and\s+(\d+)", ql)
        if amt_between:
            lo = min(int(amt_between.group(1)), int(amt_between.group(2)))
            hi = max(int(amt_between.group(1)), int(amt_between.group(2)))
            filters["_amount_min"] = {str(lo)}
            filters["_amount_max"] = {str(hi)}
        amt_min = re.search(r"(?:above|over|greater than|more than)\s+(\d+)", ql)
        if amt_min:
            filters["_amount_min"] = {amt_min.group(1)}
        amt_max = re.search(r"(?:below|under|less than)\s+(\d+)", ql)
        if amt_max:
            filters["_amount_max"] = {amt_max.group(1)}
        if "high value" in ql or "high-value" in ql:
            filters["_amount_min"] = {"2000"}
        # Time filters
        between_h = re.search(r"(?:between|from)\s+([01]?\d|2[0-3])\s*(am|pm)?\s+(?:and|to)\s+([01]?\d|2[0-3])\s*(am|pm)?", ql)
        if between_h:
            h1 = self._to_24h(between_h.group(1), between_h.group(2))
            h2 = self._to_24h(between_h.group(3), between_h.group(4))
            filters["_hour_min"] = {str(min(h1, h2))}
            filters["_hour_max"] = {str(max(h1, h2))}
        at_h = re.search(r"\bat\s+([01]?\d|2[0-3])\s*(am|pm)?\b", ql)
        if at_h:
            filters["hour_of_day"].add(str(self._to_24h(at_h.group(1), at_h.group(2))))
        after_h = re.search(r"\bafter\s+([01]?\d|2[0-3])\s*(am|pm)?\b", ql)
        if after_h:
            filters["_hour_min"] = {str(self._to_24h(after_h.group(1), after_h.group(2)))}
        before_h = re.search(r"\bbefore\s+([01]?\d|2[0-3])\s*(am|pm)?\b", ql)
        if before_h:
            filters["_hour_max"] = {str(self._to_24h(before_h.group(1), before_h.group(2)))}
        return dict(filters)

    def _best_dimension_from_filters(self, filters):
        for k in [
            "device_type",
            "network_type",
            "transaction type",
            "merchant_category",
            "sender_age_group",
            "sender_state",
            "sender_bank",
            "receiver_bank",
            "day_of_week",
        ]:
            if k in filters:
                return k
        return "transaction type"

    def _passes(self, row, filters):
        for col, allowed in filters.items():
            if col == "_amount_min":
                if row["amount_value"] < _num(next(iter(allowed), "0"), 0.0):
                    return False
                continue
            if col == "_amount_max":
                if row["amount_value"] > _num(next(iter(allowed), "0"), 0.0):
                    return False
                continue
            if col == "_hour_min":
                if row["hour_value"] < int(_num(next(iter(allowed), "0"), 0)):
                    return False
                continue
            if col == "_hour_max":
                if row["hour_value"] > int(_num(next(iter(allowed), "23"), 23)):
                    return False
                continue
            if row.get(col, "") not in allowed:
                return False
        return True

    def _aggregate_count(self, filters, dimension):
        c = Counter()
        for row in self.data.rows:
            if self._passes(row, filters):
                c[row.get(dimension, "Unknown")] += 1
        return [{"group": k, "value": v, "count": v} for k, v in c.items() if k]

    def _aggregate_avg(self, filters, dimension):
        sums = Counter()
        cnts = Counter()
        for row in self.data.rows:
            if self._passes(row, filters):
                g = row.get(dimension, "Unknown")
                sums[g] += row["amount_value"]
                cnts[g] += 1
        out = []
        for g, n in cnts.items():
            out.append({"group": g, "value": (sums[g] / n if n else 0), "count": n})
        return out

    def _aggregate_sum(self, filters, dimension):
        sums = Counter()
        cnts = Counter()
        for row in self.data.rows:
            if self._passes(row, filters):
                g = row.get(dimension, "Unknown")
                sums[g] += row["amount_value"]
                cnts[g] += 1
        return [{"group": g, "value": sums[g], "count": cnts[g]} for g in sums.keys()]

    def _aggregate_minmax(self, filters, dimension, mode="max"):
        vals = {}
        cnts = Counter()
        for row in self.data.rows:
            if self._passes(row, filters):
                g = row.get(dimension, "Unknown")
                amt = row["amount_value"]
                if g not in vals:
                    vals[g] = amt
                elif mode == "max":
                    vals[g] = max(vals[g], amt)
                else:
                    vals[g] = min(vals[g], amt)
                cnts[g] += 1
        return [{"group": g, "value": vals[g], "count": cnts[g]} for g in vals.keys()]

    def _aggregate_rate(self, filters, dimension, numerator_field):
        nums = Counter()
        dens = Counter()
        for row in self.data.rows:
            if self._passes(row, filters):
                g = row.get(dimension, "Unknown")
                dens[g] += 1
                if numerator_field == "success":
                    nums[g] += 1 if row["failed_value"] == 0 else 0
                else:
                    nums[g] += int(row.get(numerator_field, 0))
        out = []
        for g, den in dens.items():
            out.append({"group": g, "value": (nums[g] * 100.0 / den if den else 0), "count": den})
        return out

    def _ranked_answer(self, rows, metric_label, unit, question, filters, dimension, top_n=20, ascending=False):
        clean = [r for r in rows if r["group"] not in ("", "NULL", "None")]
        if not clean:
            return {
                "answer_text": "No matching data found for that question. Try removing some filters.",
                "table": {"columns": ["Group", metric_label], "rows": []},
                "chart": None,
                "dimension": None,
                "metric": None,
                "explanation": "The selected filters returned zero rows in the dataset.",
            }

        sorted_rows = sorted(clean, key=lambda x: x["value"], reverse=not ascending)
        lead = sorted_rows[0]
        tail = sorted_rows[-1]
        if metric_label.lower().endswith("rate"):
            val_fmt = lambda v: f"{v:.2f}%"
        elif unit == "INR":
            val_fmt = lambda v: f"INR {v:,.2f}"
        else:
            val_fmt = lambda v: f"{int(v):,}" if math.isclose(v, round(v)) else f"{v:,.2f}"

        chart_type = "line" if dimension in ("hour_of_day", "day_of_week") else "bar"
        order_rows = self._ordered_for_chart(clean, dimension, sorted_rows, chart_type)
        labels = [r["group"] for r in order_rows]
        values = [round(r["value"], 4) for r in order_rows]
        top_k = sorted_rows[: min(max(top_n, 1), 50)]
        top3 = top_k[:3]
        top3_text = "; ".join([f"{r['group']} ({val_fmt(r['value'])})" for r in top3]) if top3 else "No groups"

        filter_text = self._format_filters(filters)
        answer_text = (
            f"{metric_label} by {self.FIELD_LABELS.get(dimension, dimension or 'group')}: "
            f"{'lowest' if ascending else 'highest'} is {lead['group']} at {val_fmt(lead['value'])}. "
            f"{'Highest' if ascending else 'Lowest'} is {tail['group']} at {val_fmt(tail['value'])}. "
            f"Top segments: {top3_text}. {filter_text}"
        ).strip()

        table_rows = []
        for r in top_k:
            table_rows.append(
                {
                    "Group": r["group"],
                    metric_label: val_fmt(r["value"]),
                    "Sample Size": int(r.get("count", 0)),
                }
            )

        return {
            "answer_text": answer_text,
            "table": {"columns": ["Group", metric_label, "Sample Size"], "rows": table_rows},
            "chart": {
                "type": chart_type,
                "title": f"{metric_label} by {self.FIELD_LABELS.get(dimension, dimension or 'group')}",
                "labels": labels,
                "datasets": [{"label": metric_label, "data": values}],
            },
            "dimension": dimension,
            "metric": self._metric_name(metric_label),
            "explanation": f"Computed from {sum(int(r.get('count', 0)) for r in clean):,} matching transactions.",
            "question": question,
        }

    def _single_rate_answer(self, filters, numerator_field, label, question):
        num = 0
        den = 0
        for row in self.data.rows:
            if self._passes(row, filters):
                den += 1
                num += int(row.get(numerator_field, 0))
        rate = (num * 100.0 / den) if den else 0.0
        filter_text = self._format_filters(filters)
        text = f"{label} is {rate:.2f}% ({num:,} out of {den:,} transactions). {filter_text}"
        return {
            "answer_text": text,
            "table": {
                "columns": ["Metric", "Value"],
                "rows": [
                    {"Metric": label, "Value": f"{rate:.2f}%"},
                    {"Metric": "Flagged count", "Value": f"{num:,}"},
                    {"Metric": "Total matching transactions", "Value": f"{den:,}"},
                ],
            },
            "chart": {
                "type": "pie",
                "title": label,
                "labels": ["Flagged", "Not flagged"],
                "datasets": [{"label": label, "data": [num, max(den - num, 0)]}],
            },
            "dimension": None,
            "metric": "flagged_rate",
            "explanation": f"Computed from {den:,} matching transactions.",
            "question": question,
        }

    def _single_metric_answer(self, filters, metric, question):
        den = 0
        fail = 0
        flagged = 0
        total_amt = 0.0
        min_amt = None
        max_amt = None
        for row in self.data.rows:
            if self._passes(row, filters):
                den += 1
                fail += row["failed_value"]
                flagged += row["fraud_value"]
                amt = row["amount_value"]
                total_amt += amt
                min_amt = amt if min_amt is None else min(min_amt, amt)
                max_amt = amt if max_amt is None else max(max_amt, amt)
        if den == 0:
            return {
                "answer_text": "No matching rows for that question.",
                "table": {"columns": ["Metric", "Value"], "rows": []},
                "chart": None,
                "dimension": None,
                "metric": metric,
                "explanation": "The selected filters returned zero rows in the dataset.",
                "question": question,
            }
        mapping = {
            "count": ("Transactions", float(den), ""),
            "failure_rate": ("Failure rate", fail * 100.0 / den, "%"),
            "success_rate": ("Success rate", (den - fail) * 100.0 / den, "%"),
            "flagged_rate": ("Flagged rate", flagged * 100.0 / den, "%"),
            "avg_amount": ("Average amount", total_amt / den, "INR"),
            "sum_amount": ("Total amount", total_amt, "INR"),
            "min_amount": ("Minimum amount", float(min_amt or 0), "INR"),
            "max_amount": ("Maximum amount", float(max_amt or 0), "INR"),
        }
        label, value, unit = mapping.get(metric, ("Transactions", float(den), ""))
        value_text = f"{value:.2f}%" if unit == "%" else (f"INR {value:,.2f}" if unit == "INR" else f"{int(value):,}")
        return {
            "answer_text": f"{label}: {value_text}. {self._format_filters(filters)}",
            "table": {"columns": ["Metric", "Value"], "rows": [{"Metric": label, "Value": value_text}, {"Metric": "Sample Size", "Value": f"{den:,}"}]},
            "chart": None,
            "dimension": None,
            "metric": metric,
            "explanation": f"Computed from {den:,} matching transactions.",
            "question": question,
        }

    def _format_filters(self, filters):
        if not filters:
            return "No extra filters applied."
        chunks = []
        for k, vals in filters.items():
            if k == "_amount_min":
                chunks.append(f"amount >= INR {int(_num(next(iter(vals), '0'), 0))}")
            elif k == "_amount_max":
                chunks.append(f"amount <= INR {int(_num(next(iter(vals), '0'), 0))}")
            elif k == "_hour_min":
                chunks.append(f"hour >= {int(_num(next(iter(vals), '0'), 0))}")
            elif k == "_hour_max":
                chunks.append(f"hour <= {int(_num(next(iter(vals), '23'), 23))}")
            else:
                chunks.append(f"{k} in ({', '.join(sorted(vals))})")
        return "Filters: " + "; ".join(chunks) + "."

    def _metric_name(self, metric_label):
        t = metric_label.lower()
        if "failure" in t:
            return "failure_rate"
        if "success" in t:
            return "success_rate"
        if "average amount" in t:
            return "avg_amount"
        if "flagged" in t:
            return "flagged_rate"
        if "total amount" in t:
            return "sum_amount"
        if "minimum amount" in t:
            return "min_amount"
        if "maximum amount" in t:
            return "max_amount"
        return "count"

    def _alias_to_column(self, text):
        t = text.strip().lower()
        if t in self.DIMENSION_ALIASES:
            return self.DIMENSION_ALIASES[t]
        for k, v in self.DIMENSION_ALIASES.items():
            if k in t:
                return v
        return None

    def _is_followup(self, ql):
        return any(x in ql for x in ["what about", "how about", "same", "those", "that", "them", "and for"])

    def _should_use_time_series(self, ql):
        return "peak hour" in ql or ("hour" in ql and any(x in ql for x in ["peak", "trend", "over time"]))

    def _needs_grouping(self, ql):
        return any(x in ql for x in ["which", "compare", "comparison", "by ", "across", "for each", "top", "bottom", "highest", "lowest", "segment"])

    def _prefer_single_value(self, ql, metric):
        if "percentage of" in ql and metric in ("flagged_rate", "failure_rate", "success_rate"):
            return True
        return not self._needs_grouping(ql)

    def _detect_rank(self, ql):
        n = 20
        ascending = any(x in ql for x in ["lowest", "bottom", "least", "smallest"])
        m = re.search(r"\btop\s+(\d+)\b", ql)
        if m:
            n = int(m.group(1))
            ascending = False
        m2 = re.search(r"\bbottom\s+(\d+)\b", ql)
        if m2:
            n = int(m2.group(1))
            ascending = True
        return n, ascending

    def _ordered_for_chart(self, clean, dimension, sorted_rows, chart_type):
        if chart_type != "line":
            return sorted_rows
        if dimension == "hour_of_day":
            return sorted(clean, key=lambda x: int(_num(str(x["group"]), 0)))
        if dimension == "day_of_week":
            day_pos = {d.capitalize(): i for i, d in enumerate(self.DAYS)}
            return sorted(clean, key=lambda x: day_pos.get(x["group"], 99))
        return sorted(clean, key=lambda x: x["group"])

    def _to_24h(self, hour_str, ampm):
        h = int(hour_str)
        a = (ampm or "").lower()
        if a == "pm" and h < 12:
            h += 12
        if a == "am" and h == 12:
            h = 0
        return h


class AppHandler(BaseHTTPRequestHandler):
    data = DataEngine(CSV_PATH)
    engine = QueryEngine(data)

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _text(self, body: bytes, ctype: str, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/" or p == "/index.html":
            body = (WEB_DIR / "index.html").read_bytes()
            return self._text(body, "text/html; charset=utf-8")
        if p == "/app.js":
            body = (WEB_DIR / "app.js").read_bytes()
            return self._text(body, "application/javascript; charset=utf-8")
        if p == "/styles.css":
            body = (WEB_DIR / "styles.css").read_bytes()
            return self._text(body, "text/css; charset=utf-8")
        if p == "/health":
            return self._json({"ok": True, "rows": self.data.total, "columns": self.data.columns})
        return self._json({"error": "Not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/ask":
            return self._json({"error": "Not found"}, 404)
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        try:
            payload = json.loads(raw.decode("utf-8"))
            q = str(payload.get("question", "")).strip()
            if not q:
                return self._json({"error": "Question is required."}, 400)
            out = self.engine.ask(q)
            return self._json(out, 200)
        except Exception as e:
            return self._json({"error": f"Bad request: {e}"}, 400)


def run():
    server = HTTPServer(("127.0.0.1", 8000), AppHandler)
    print("Server running at http://127.0.0.1:8000")
    server.serve_forever()


if __name__ == "__main__":
    run()
