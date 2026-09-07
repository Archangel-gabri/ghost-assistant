# Ghost — контекст и блокеры текущего WIP

Desktop voice assistant: PySide6/Qt, STT и CLI-адаптеры LLM.
Запуск живой модели/микрофона/передача screenshot — не замена локальному тесту.

## Checkpoint 2026-09-07

- Baseline HEAD: `0d494b81f86e01ba72361fc3ca1868a9a477e42d`.
- Исходный orchestrator WIP был отклонён; после RED → GREEN добавлены transport
  lifecycle, bounded nonstream I/O, CLI/provider/screenshot contracts, STT process,
  audio cleanup и queued UI lifecycle. Существующие тесты не удалять.
- **65 PASS и 216 PASS — исторические checkpoints**, их hashes не покрывают
  последующие изменения. Текущий gate: **243 PASS** с `GHOST_NATIVE_CODEX_CONFIG=1`;
  default — **242 PASS / 1 skip**, в том числе в свежем минимальном CI-окружении.
  Opt-in native config-only probe намеренно завершается до inference на неизвестном
  sentinel-поле и не вызывает модель.
- Offline-проверки используют настоящие локальные subprocesses с synthetic CLI/STT
  backends, fake capture и настоящую offscreen Qt-очередь. Это проверяет процессы,
  pipes, отмену и маршруты сигналов, но не native speech inference или live-модели.
- Ранние native Claude-пробы 7 сентября вернули валидный stream-json, но terminal result
  с `subtype=success` имел `is_error=true`, а child завершился с `rc=1`:
  **HTTP 403 `model_not_permitted` — отказ тех проб**, не malformed JSON.
  Явный `--model sonnet` дал тот же отказ. Пропущенный model pin исправлен отдельно
  и не был причиной того native failure. Поздняя отдельно согласованная проба
  с явным owner-model `opus[1m]` тоже завершилась ошибкой; ещё одна разрешённая
  диагностика дала `api_error`, без восстановленного HTTP-кода. Приписывать этому
  Opus-запуску прежний 403 нельзя. Auth/payment-настройки не менялись; дальнейших
  вызовов после этих ограниченных проб не было.
- Ранняя native Codex-проба выявила неправильный quoted dotted project-key override; исправлен
  целой TOML inline table с точным cwd и `trust_level="untrusted"`, strict-config сохранён.
  Следующая ранняя direct-OpenAI text-проба дошла до auth/permission diagnostics (401/403) и
  завершилась timeout через 45,016 s; owned processes после cleanup — 0.
  Изображение тогда после text failure не отправлялось. Это исторический результат
  другого provider choice, не текущий blocker проверенного owner router.
- Поздняя отдельно согласованная Codex-проба с явным действующим router и
  `gpt-5.6-sol` вернула точный текстовый маркер за 11.398 s. Ещё одна разрешённая
  проба через реальный Ghost staging/native `--image` вернула точный `HARBOR 628`
  за 11.463 s: маркера не было в текстовом prompt, desktop capture = 0. После каждой
  пробы owned processes = 0; временные изображения удалены. Auth/global config
  не менялись. Доказательство ограничено этим route/model и синтетическим входом.
- Единственная согласованная native STT-проба использовала существующий cached base,
  offline/local-files-only, CPU/int8 и искусственный русский голос; без скачивания
  моделей и микрофона. Текст вернулся за 10.231 s, но совпадений с заранее заданными
  смысловыми основами **0/5: quality FAIL**. Child завершён, WAV удалён; при shutdown
  resource tracker сообщил об одном semaphore. После tracker этот объект отсутствовал,
  но чистый native-library shutdown не доказан. Одна synthetic-voice fixture не
  разделяет причины между синтезатором, моделью и аудиотрактом.
- Отдельный owner-local launcher: **31 PASS** и независимое принятие с оговоркой
  о доверии установленным system/package code, **не OS sandbox**. Реальный `main.main()`
  открыл и закрыл видимое xcb-окно без worker, capture или model request. Launcher,
  Desktop Exec-правки и ignored local profile не входят в переносимый публичный пакет.
- `stage: shipped` относится к прежнему состоянию, не приёмке этого WIP.

## Реализованные контракты и границы доказательств

`LLMResult(str)` несёт outcome каждого запроса: success/error/timeout/cancelled/mock.
Chunks предварительные: только success становится final/history. Ошибка после
chunk заменяет draft; успешный буквальный `[timeout]` не ошибочно распознаётся как сбой.
Claude stream и nonstream-пути используют binary pipes/selectors с deadline.
В nonstream prompt/stdout ограничены 2 MiB каждый, stderr хранится хвостом 8 KiB;
проверены byte boundaries, UTF-8, duplex pressure и раннее закрытие stdin.
Stream дополнительно ограничивает JSON-строку 1 MiB и накопленный текст 2 MiB.
Неполный протокол/ненулевой exit/пустой ответ не считаются успехом; финальный result
авторитетен. Факт успешной записи prompt не доказывает, что внешняя CLI его прочитала.

`close()` запрещает новые запросы; повторный вызов может повторить незавершённый
cleanup. POSIX cleanup обрабатывает собственную process group даже после exit 0
родителя и reap-ит direct child. При неуспехе ownership сохраняется. Потомки,
намеренно сменившие session/process group, в эту гарантию не входят.

STT вынесен в lazy persistent `spawn`-child: backend загружается при первом запросе,
JSON IPC ограничен 16 KiB на запрос / 64 KiB на ответ. Отмена/timeout/повреждённые
frames проверяются с synthetic backend в настоящем процессе. AudioCapture держит
одну транскрипцию и уникальный WAV; удаляет вход после завершения/reap, а при
незавершённом cleanup сохраняет путь для безопасной повторной уборки. Ошибки STT
идут отдельным error callback, а не становятся вопросом для LLM.

Stop не ждёт в GUI. Worker identity + generation отсекают поздние Qt-события;
restart/quit требуют подтверждённого cleanup. При его отказе worker удерживается,
перезапуск и выход блокируются с видимой ошибкой; новая сессия не запускается поверх старой.

Codex требует явный `codex_provider_spec`; UI берёт его из выбранного tool entry,
ConfigBuilder/API — из backend config. Встроенный `Codex (OpenAI)` явно выбирает
OpenAI. Custom provider задаёт nonsecret metadata и один способ auth: имя env var
либо `requires_openai_auth: true`. Provider из ambient config не импортируется;
отсутствующая/неверная specification даёт ошибку до model spawn. Не подменять
настройки владельца OpenAI-default без его выбора.

Claude получает safe-mode/restricted/Read/strict-MCP flags; Codex — ignore-user-config,
ignore-rules, отключение hooks/plugins/apps и ограничения project context.
Invocation contract ориентирован на Claude Code 2.1.263 / Codex 0.153.4. Проверка
аргументов и hostile fixtures не доказывает полную изоляцию установленной CLI:
machine-managed policy остаётся вне контроля приложения. Generic commands имеют
bounded I/O/cancellation, но не получают этот provider-specific isolation contract.

Screenshot вне cwd читается через no-follow descriptors, ограничен 8 MiB, проверяются
regular-file/type signature и неизменность во время чтения. Это не полная проверка
валидности изображения. Временный private snapshot живёт до завершения запроса:
Claude получает его путь и `Read` через `--add-dir` только snapshot-каталога;
Codex получает native `--image`; generic screenshot отклоняется. Synthetic тесты
проверяют bytes/argv/cleanup. Отдельная native-проба подтвердила интерпретацию одного
синтетического изображения через указанный Codex route/model; Claude image handling
и точность реального desktop capture остаются непроверенными.

## Что остаётся до приёмки

1. Claude inference не подтверждён: текущая Opus-проба дала `api_error`, точная причина
   не установлена; прежний Sonnet дал `model_not_permitted`. Не повторять model-пробы
   без отдельного текущего разрешения; auth/payment автоматически не менять. Valid JSON
   и `subtype=success` не означают успех при `is_error=true` и ненулевом child exit.
2. Исследовать native STT quality FAIL и semaphore shutdown warning отдельным bounded
   планом. Одна synthetic-voice fixture не доказывает ни качество живой речи, ни причину
   ошибки. Не скачивать модели и не запускать микрофон или новые пробы автоматически.
3. Реальные audio capture → STT → model → UI, Stop/restart и native cleanup требуют
   отдельной приёмки. Нативное GUI-окно без Start и public offline tests её не заменяют.
4. Codex text и synthetic image для проверенного router прошли; Claude image,
   desktop-capture fidelity и полная provider/managed-policy изоляция ещё не доказаны.
   `--sandbox read-only --ephemeral` само по себе не isolation proof. Сохранять final
   gate, exact whitelist/hashes и независимый review; owner-local launcher не выдавать
   за часть переносимого публичного installer.

Локальный gate без установки нового окружения (system Python 3.14.5 содержит Qt/numpy;
существующий pytest находится в отдельном uv tool environment):

```bash
PYTHONPATH=/home/danya/.local/share/uv/tools/pytest/lib/python3.14/site-packages \
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests --tb=short
ruff check src tests
git diff --check
```

Для дополнительной config-only проверки установленного Codex 0.153.4 добавить
`GHOST_NATIVE_CODEX_CONFIG=1`. Она не проверяет inference или доступ аккаунта.

Обычный dev setup — [CONTRIBUTING.md](CONTRIBUTING.md). Нет новых commit/push;
полный gate и точный whitelist обязательны перед публикацией.
