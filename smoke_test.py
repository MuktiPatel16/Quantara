import app


def main():
    engine = app.QueryEngine(app.DataEngine(app.CSV_PATH))
    questions = [
        "Which transaction type has the highest failure rate?",
        "How do failure rates compare between Android and iOS users?",
        "What are peak transaction hours for Grocery on weekends?",
        "What percentage of high-value transactions are flagged for review?",
    ]
    for q in questions:
        r = engine.ask(q)
        print("=" * 80)
        print("Q:", q)
        print("A:", r.get("answer_text"))
        print("Explanation:", r.get("explanation"))
        print("Rows:", len(r.get("table", {}).get("rows", [])))


if __name__ == "__main__":
    main()

