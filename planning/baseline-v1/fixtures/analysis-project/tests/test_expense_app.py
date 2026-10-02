import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import expense_app


class ExpenseAppTests(unittest.TestCase):
    def test_add_and_sum_expenses(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(expense_app, "DATA_FILE", Path(folder) / "expenses.json"):
                expense_app.add_expense("Lunch", 12.5)
                expense_app.add_expense("Bus", 4)
                self.assertEqual(expense_app.total_expenses(), 16.5)


if __name__ == "__main__":
    unittest.main()
