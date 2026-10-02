import argparse
import json
from pathlib import Path

DATA_FILE = Path(__file__).with_name("expenses.json")


def load_expenses():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def add_expense(description, amount):
    expenses = load_expenses()
    expenses.append({"description": description, "amount": round(float(amount), 2)})
    DATA_FILE.write_text(json.dumps(expenses, indent=2), encoding="utf-8")
    return expenses[-1]


def total_expenses():
    return round(sum(item["amount"] for item in load_expenses()), 2)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("description")
    add.add_argument("amount", type=float)
    sub.add_parser("total")
    args = parser.parse_args()
    if args.command == "add":
        add_expense(args.description, args.amount)
        print("Expense added")
    else:
        print(f"Total: {total_expenses():.2f}")


if __name__ == "__main__":
    main()
