# Training Pipeline

## Данные
- `../plans.jsonl` — экспорт подтверждённых планов.
- Формат: одна строка = JSON с полями `intake`, `codes`, `plan_text`, `feedback`, метаданными врача/пациента.

## Вариант A: LoRA на Llama 3.1 70B Instruct
- Требования: GPU >= 48 GB VRAM (A6000, H100), 200+ GB диска.
- Фреймворки: `transformers`, `peft`, `accelerate`, `trl`.
- Шаги:
  1. Подготовить dataset (`datasets.Dataset.from_json('training/plans.jsonl')`).
  2. Сформировать промпты (см. `prepare_prompts.py`).
  3. Запустить LoRA (см. `lora_finetune.py`).
  4. Экспортировать адаптер, протестировать через vLLM.

## Вариант B: DPO/ORPO на gpt-4o-mini через API
- Требования: quota OpenAI, `openai` SDK.
- Используем позитивные/негативные примеры из `feedback`.
- Скрипт: `dpo_api.py` — отправляем пары (prompt, preferred, rejected) на `responses.create`.
- Плюс: без GPU. Минус: стоимость токенов и зависимость от внешнего API.

## План
1. `export_training_dataset.py` (готово).
2. `prepare_prompts.py` — генерация SFT/DPO-пар.
3. `lora_finetune.py` + `dpo_api.py` — каркас обучения.
4. Планировщик (Windows Task Scheduler/cron или CI) — запуск раз в неделю.
