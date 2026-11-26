# Локальные замеры

Запустите `python -m pytest -m perf -q -s` или `python benchmarks/measure.py` из корня репозитория.
Стенд использует только `httpx.MockTransport`, вымышленные данные и подставные часы.
Обычный `pytest` пропускает эти проверки. Замеры и сравнение со старым SDK описаны в [документации](../docs/PERFORMANCE.md).
