# AZ-700 Exam Simulator — веб-версия на K3s

## Context

Существующий десктопный тренажёр AZ-700 (`sample/`, tkinter + модулитный бэкенд) полностью реализован и покрыт тестами: 369 вопросов нарезаны из 74 PDF в 738 PNG, каталог и статистика лежат в SQLite. Работает он только на одной машине под Windows.

Нужна веб-версия того же функционала, чтобы заниматься с любого устройства — **включая смартфон**. Приложение однопользовательское; HA и многопользовательский доступ не нужны. Доступ ограничивается хардкод-логином `dima` без пароля.

Результат: публичный Docker-образ в `ghcr.io/divlv/exam700`, развёрнутый как pod со стандартным приоритетом на личном K3s-сервере пользователя (`myk3s`, `37.27.214.42`), с SQLite и картинками на диске ноды и HTTPS на `az700.v1.lv`. Манифесты пользователь применяет вручную.

### Исходные данные, установленные при исследовании

| | |
|---|---|
| Репозиторий | `github.com/divlv/exam700`, публичный, сейчас содержит только `LICENSE`/`README.md`/`.gitignore` |
| Датасет | 738 PNG, 166.5 МБ (медиана 147 КБ, p90 415 КБ, max 3.1 МБ), ширина 1355 px, высота до 6399 px |
| Живая БД | 369 вопросов, 60 без ответа в исходнике, 12 помечены некорректными → **297 доступных**; 10 сессий, 71 оценка |
| Нода | одна, `myk3s`, **amd64**, k3s v1.33.4, Ubuntu 24.04, 98 ГБ свободно на `/` |
| **Ограничение** | нода зарезервирована на 96% памяти: свободно ≈550m CPU и ≈**497Mi** под requests |
| Namespace | `mywebs` (единственный пользовательский) |
| TLS | Traefik 3.3.6 c собственным ACME, certresolver `prod`. **cert-manager не установлен.** HTTP→HTTPS редирект глобальный на entrypoint |
| Хранилище | конвенция — ручной hostPath PV `/data/<app>`, `storageClassName: manual`, `Retain`, `DirectoryOrCreate`, 2Gi. `local-path` не используется нигде |
| Приоритеты | `low-priority` (value 10) — `globalDefault: true`; это и есть «стандартный». `high-priority` вытеснил бы работающие сайты |
| Локальное окружение | Docker **нет**, kubectl **нет**, есть `gh` 2.85 и Python 3.14.2 |

### Принятые решения

- Контент (картинки + SQLite) — на диске сервера, не в образе. Образ остаётся маленьким, а нарезка чужих экзаменационных дампов не попадает в публичный репозиторий и публичный registry.
- Сборка — GitHub Actions → GHCR (локального Docker нет). Публичный пакет ⇒ `imagePullSecrets` не нужен.
- Стек — FastAPI + Jinja2, серверный рендеринг. Слой `web/` заменяет `gui/`, `api/modules/*` переиспользуется.
- Переносим все семь экранов 1:1.
- План сессии сохраняется в БД (см. ADR ниже) — иначе мобильный браузер, выгрузив вкладку, потеряет незавершённый экзамен.
- Service — `ClusterIP:80`, не `LoadBalancer`: каждый LB порождает лишний pod `svclb-*` (их уже 11), а памяти на ноде в обрез. Совпадает с новейшим стилем `spisokvdorogu-*`.
- Тег `:latest` + `imagePullPolicy: Always`; обновление = push → CI → `drestart az700` на сервере, манифест править не нужно.
- Контейнер от uid 1000; `/data/az700` создаётся вручную с `chmod 777` — ровно как `kuber.sh` уже делает для `/data/postgres`.
- Картинки отдаются как есть, с иммутабельным кэшем и предзагрузкой следующей.

---

## Раскладка репозитория

Всё новое — в корне `exam700`. Каталоги `sample/` и `sample-k3s/` остаются untracked референсами; **их не трогаем**.

```
api/modules/{shared,questionbank,examsession,ingest}/   перенос из sample/ без изменений API
web/
  main.py            фабрика FastAPI, lifespan (миграции), SessionMiddleware, статика
  deps.py            соединение SQLite на запрос, require_login
  auth.py            /login, /logout, проверка логина
  routes/            menu, session, exam, results, admin, settings, images, health
  templates/         base.html + по шаблону на экран
  static/            app.css, app.js, manifest.json, icon-192.png, icon-512.png
tools/build_dataset.py                 offline-утилита под Windows (без изменений)
tests/                                 перенос модульных тестов + новые web-тесты
CONTEXT.md                             глоссарий предметной области
docs/deploy-k3s.md                     операторская инструкция
docs/adr/0001-persist-session-plan.md
deploy/k3s/                            манифесты и скрипты
Dockerfile
requirements.txt          fastapi, uvicorn[standard], jinja2, itsdangerous
requirements-ingest.txt   pymupdf, pillow          (только для Windows, в образ не идёт)
requirements-dev.txt      pytest, httpx
.github/workflows/docker.yml
```

---

## 1. Бэкенд: что переиспользуется и что меняется

`api/modules/*` не импортирует tkinter — контракт переносится целиком. Изменения минимальны и не ломают десктопную версию и существующие тесты.

### 1.1 `shared` — конфигурация путей через env

Сейчас `sample/api/modules/shared/api.py` жёстко считает `PROJECT_ROOT = Path(__file__).resolve().parents[3]` и всё от него. Конфигурации нет вообще (проверено: ни `os.environ`, ни `config.json`).

Добавить перекрытие переменными окружения, **сохранив текущие значения по умолчанию**:

| Переменная | По умолчанию | В контейнере |
|---|---|---|
| `AZ700_DATA_DIR` | `<root>/data` | `/data/az700` |
| `AZ700_DB_PATH` | `$AZ700_DATA_DIR/az700.sqlite` | наследуется |
| `AZ700_LOGS_DIR` | `<root>/logs` | `/data/az700/logs` |

`IMAGES_DIR`/`REPORT_DIR` считаются от `AZ700_DATA_DIR`. Пути в БД (`images/q0001_question.png`) — относительные к `DATA_DIR`, менять их не нужно.

### 1.2 `shared/internal/_db.py` — соединение, пригодное для ASGI

`connect()` сейчас вызывает `sqlite3.connect(db_path)` без `check_same_thread` и без таймаута; GUI держит одно соединение на весь процесс в Tk-потоке. Для веба:

- `check_same_thread=False`, `timeout=10.0`;
- добавить `PRAGMA busy_timeout = 10000` рядом с уже существующими `foreign_keys = ON` и `journal_mode = WAL`;
- соединение — **на запрос**, через зависимость FastAPI (`web/deps.py`), с закрытием в `finally`. Пула нет: один пользователь, один воркер.

`PRAGMA foreign_keys = ON` обязателен — на нём держится `ON DELETE CASCADE` при сбросе сессии.

### 1.3 `examsession` — схема v2, сохранение плана сессии

Единственное содержательное изменение домена. Сейчас в `sessions` лежит только `planned_count`; вытянутый список вопросов и порядок живут в памяти `SessionRunner`.

Миграция v2 (механизм `apply_migrations` уже есть и идемпотентен, применится к вашей базе автоматически при первом старте):

```sql
CREATE TABLE IF NOT EXISTS session_questions (
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    PRIMARY KEY (session_id, position)
);
CREATE INDEX IF NOT EXISTS idx_session_questions_session ON session_questions(session_id);
```

`question_id` намеренно **без** внешнего ключа — тот же принцип, что уже применён к `broken_questions` и `session_answers`: история и отметки должны переживать пересборку каталога.

Новое в `examsession/api.py`:

- `start_session(...)` — дополнительно пишет план в `session_questions`;
- `resume_session(conn, session_id) -> SessionRunner | None` — восстанавливает runner из `session_questions` + `session_answers`;
- `get_active_session(conn) -> ExamSession | None` — свежайшая со `status = 'in_progress'`.

`SessionRunner.flag_broken()` при замене вопроса обязан переписать строку плана на той же `position`; при исчерпании пула — удалить хвостовую строку, чтобы `total` в БД совпадал с `total` в памяти.

Семантику **не меняем**: оценки `correct`/`partial`/`incorrect`, порог 70% только по `correct`, ретроактивное исключение оценок помеченных вопросов из всей статистики, `excluded = recorded - tally.total`.

---

## 2. Веб-слой

Post-Redirect-Get на всех действиях — чтобы «назад» и перезагрузка на телефоне не пересылали форму.

| Маршрут | Назначение |
|---|---|
| `GET/POST /login`, `POST /logout` | одно поле «логин», сверка с `dima` |
| `GET /` | главное меню: доступно вопросов, пройдено сессий, отметки; баннер «Продолжить сессию», если есть активная |
| `GET/POST /session/new` | единственная опция — количество вопросов (пресеты 10/20/30/50/100 + произвольное, потолок = доступным) |
| `GET /exam` | текущий вопрос |
| `GET /exam/answer` | ответ + кнопки оценки (отдельный URL вместо флага в памяти) |
| `POST /exam/grade` · `/flag` · `/abandon` | действия |
| `GET /results` · `GET /results/{id}` | сводка и разбор сессии |
| `GET /admin` + POST-действия | три вкладки: статистика/сбросы, некорректные вопросы, без ответа в исходнике |
| `GET/POST /settings` | количество по умолчанию, авто-показ ответа |
| `GET /img/{name}.png` | отдача из `DATA_DIR/images`, имя валидируется по `^q\d{4}_(question\|answer)\.png$`, `Cache-Control: public, max-age=31536000, immutable` + ETag |
| `GET /healthz` | для проб, без авторизации |

**Авторизация.** `starlette.middleware.sessions.SessionMiddleware` с ключом из `AZ700_SESSION_SECRET`; cookie HttpOnly + Secure + SameSite=Lax, `max_age` 90 дней, чтобы телефон не разлогинивался. Зависимость `require_login` редиректит на `/login`. Логин `dima` — константа в `web/auth.py`. `/healthz`, `/login` и `/static` — единственные открытые маршруты.

**Логирование.** Переиспользуем `shared.get_logger` с существующей схемой RunId (7 символов `[a-z0-9]`, формат `... [RunId: 39z64rf] ...`). В контейнере — консоль (её собирает k8s) плюс ротируемый файл в `/data/az700/logs`. Логируем: старт и загрузку конфигурации, применённые миграции, вход/выход, начало и завершение сессии, каждую оценку, отметку некорректного вопроса, сбросы, необработанные исключения. Каждый UI-переход не логируем.

---

## 3. Мобильная работа

Один адаптивный шаблон, breakpoint 768px. Отдельной мобильной версии нет.

- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">` — **без** `user-scalable=no`, нативный pinch-zoom должен работать.
- **Липкая панель действий внизу.** Ключевое решение: медианная картинка вопроса выше экрана телефона в 5–6 раз, прокручивать её до конца ради кнопки «Правильно» неприемлемо. Панель `position: sticky; bottom: 0` с учётом `env(safe-area-inset-bottom)`.
- Зоны нажатия ≥48px, кнопки оценок во всю ширину с разделением.
- «Вопрос некорректный» и «Прервать» — только через подтверждение.
- F2-попап «перечитать вопрос» на широком экране остаётся отдельной панелью; на узком заменяется переключателем **Вопрос ⇄ Ответ** на месте.
- Просмотр картинки: по умолчанию по ширине; кнопки −/+/по ширине/1:1 работают по тапу; двойной тап переключает «по ширине ⇄ 1:1»; Ctrl+колесо на десктопе.
- Горячие клавиши десктопа сохраняются: Пробел — показать ответ, 1/2/3 — оценка, Esc — прервать, F2 — перечитать.
- Таблицы результатов и разбора на узком экране рендерятся карточками, без горизонтальной прокрутки.
- Предзагрузка: сервер знает план сессии, поэтому в шаблон передаётся картинка ответа текущего вопроса и вопроса следующего — оба греются через `new Image()`.
- `manifest.json` + иконки 192/512 — добавление на домашний экран, запуск в standalone.

---

## 4. Docker и CI

**Dockerfile** — `python:3.14-slim` (совпадает с локальным 3.14.2; при отсутствии колёс для FastAPI/uvicorn откатиться на `3.13-slim` — проверить на первом прогоне CI), сборка venv отдельным слоем, пользователь uid 1000, `EXPOSE 8000`, запуск `uvicorn web.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers`. `pymupdf` в образ не ставится.

**`.github/workflows/docker.yml`**
- триггер: push в `main`;
- `permissions: {contents: read, packages: write}`;
- job `test`: Python 3.14, `pip install -r requirements.txt -r requirements-dev.txt`, `pytest -q`;
- job `build` (needs test): `docker/login-action` в `ghcr.io` с `GITHUB_TOKEN`, `docker/build-push-action` с `platforms: linux/amd64`, теги `:latest` и `:${{ github.sha }}`, кэш `type=gha`.
- После первой публикации пакет в GitHub нужно вручную переключить на Public — иначе понадобится `imagePullSecrets: github-regcred`. Шаг попадёт в инструкцию.

Перед завершением работы прогнать `actionlint` на workflow.

---

## 5. Манифесты `deploy/k3s/`

Отдельный файл на часть, как в новейшем `spisokvdorogu-*`. Все namespaced-объекты — в `mywebs`.

**`az700-storage.yaml`** — PV `az700-pv`: `hostPath: /data/az700`, `type: DirectoryOrCreate`, `2Gi`, `storageClassName: manual`, `persistentVolumeReclaimPolicy: Retain`. PVC `az700-content` в `mywebs` с `volumeName: az700-pv`. Точная копия схемы `zaharov-info-pv`/`zaharov-info-content`.

**`az700-secret.example.yaml`** — Secret `az700-secrets`, ключ `SESSION_SECRET`, значение — плейсхолдер `<SECRET>` плюс команда генерации в комментарии. Реальное значение в репозиторий не коммитим.

**`az700-app.yaml`** — Deployment + Service:
- `replicas: 1`, `strategy: Recreate` (SQLite на RWO-томе — два pod'а одновременно недопустимы);
- `priorityClassName: low-priority` с house-комментарием;
- `image: ghcr.io/divlv/exam700:latest`, `imagePullPolicy: Always`, без `imagePullSecrets`;
- `env`: `AZ700_DATA_DIR=/data/az700`, `SESSION_SECRET` из `secretKeyRef`;
- `resources: requests {cpu 50m, memory 128Mi}, limits {cpu 500m, memory 512Mi}` — гарантированно влезает в оставшиеся 497Mi;
- `securityContext`: `runAsNonRoot: true`, `runAsUser: 1000`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `capabilities: drop [ALL]`, `seccompProfile: RuntimeDefault`; `emptyDir` на `/tmp`;
- startup/readiness/liveness `httpGet /healthz` на именованный порт `http`, **`timeoutSeconds: 5`** (единственный моргающий pod в кластере страдает от `timeoutSeconds: 2`);
- том `az700-content` смонтирован в `/data/az700`;
- Service `az700`, `type: ClusterIP`, `port: 80` → `targetPort: http`.

**`az700-ingress.yaml`** — host `az700.v1.lv`, `spec.ingressClassName: traefik` (и дублирующая house-аннотация того же имени), `router.entrypoints: websecure`, `router.tls.certresolver: prod`, `router.middlewares: mywebs-server-header-mask@kubernetescrd`. Блок `tls:` содержит **только `hosts`, без `secretName`** — именно это включает ACME у Traefik. Редирект HTTP→HTTPS уже глобальный, `redirect-www` не нужен (поддомен).

**`install.sh` / `uninstall.sh`** — в стиле `mywebs_install/<fqdn>.sh`: `k3s kubectl apply -f` в порядке storage → secret → app → ingress с `sleep 1`; удаление в обратном порядке (PV с `Retain` данные не тронет).

---

## 6. Инструкция `docs/deploy-k3s.md`

Операторский документ в вашем стиле: зачем каждый шаг, команда, как проверить результат, что делать при ошибке.

1. Завести DNS-запись `az700.v1.lv` → `37.27.214.42` и дождаться распространения (`nslookup`). **До** применения ingress, иначе HTTP-01 challenge провалится.
2. Сделать пакет GHCR публичным после первой сборки.
3. Подготовить каталог и скопировать данные:
   ```bash
   ssh root@37.27.214.42 'mkdir -p /data/az700/images && chmod -R 777 /data/az700'
   scp sample/data/az700.sqlite      root@37.27.214.42:/data/az700/
   scp -r sample/data/images/*.png   root@37.27.214.42:/data/az700/images/
   ```
   166 МБ, пара минут. `/data` уже попадает в существующий `files_backup.sh`.
4. Сгенерировать `SESSION_SECRET`, заполнить `az700-secret.yaml` из примера, применить.
5. Применить манифесты по порядку, проверить каждый.
6. Проверки: pod Running, `curl -I https://az700.v1.lv/healthz`, сертификат от Let's Encrypt, вход под `dima`, главное меню показывает **297 доступных из 369**.
7. Обновление приложения: push → зелёный CI → `drestart az700` (или `k3s kubectl -n mywebs rollout restart deploy/az700`).
8. Диагностика: `Pending` → не хватило памяти, смотреть `describe pod`; ошибка сертификата → DNS или логи Traefik; `unable to open database file` → права на `/data/az700`.

Скриншот-плейсхолдеры: pod Running в Skooner, экран экзамена на телефоне, замок сертификата.

---

## 7. Домен и ADR

**`CONTEXT.md`** — только глоссарий, без деталей реализации. Термины, уточнённые в этой сессии: *доступный вопрос* (`has_answer = 1` и нет отметки), *зачтённый ответ* (оценка, чей вопрос доступен **сейчас**), *план сессии* (упорядоченный список вытянутых вопросов), *оценка* (правильно / частично / неправильно; частично не даёт частичного балла), *отметка «некорректный»* (глобальная и ретроактивная), *порог* (70% правильных среди зачтённых).

**`docs/adr/0001-persist-session-plan.md`** — почему план сессии переехал в БД. Три критерия выполняются: обратить сложно (миграция схемы), без контекста неочевидно (десктоп жил без этого), настоящий компромисс (альтернативы — память процесса и вывод из `session_answers` — рассмотрены и отвергнуты из-за поведения мобильных браузеров).

---

## 8. Проверка

**Локально (Windows):**
```cmd
pytest -q
set AZ700_DATA_DIR=C:\Work\my\AZ700ExamWebApp\sample\data
uvicorn web.main:app --reload --port 8000
```
Пройти сессию из 3 вопросов целиком: вход → меню → новая сессия → показать ответ → три оценки → разбор. Отдельно проверить: отметку некорректного вопроса с заменой, сброс сессии в админке, сохранение настроек.

**Мобильная проверка** — оба способа:
- Chrome DevTools, эмуляция iPhone/Pixel: липкая панель не перекрывает контент, кнопки не мельче 48px, таблицы стали карточками, pinch-zoom работает.
- С реального телефона по локальной сети (`uvicorn --host 0.0.0.0`, адрес `http://<ip-машины>:8000`).

**Восстановление сессии** — начать экзамен, перезапустить uvicorn, открыть `/` заново: должно предложить продолжить с той же позиции и тем же списком вопросов.

**Новые тесты:** миграция examsession v2 и идемпотентность; `resume_session` восстанавливает позицию, порядок и уже поставленные оценки; `flag_broken` корректно переписывает план; маршруты через `httpx.ASGITransport` — редирект неавторизованного на `/login`, полный цикл сессии, `/img/` отвергает выход за каталог (`../`, абсолютные пути).

**CI:** прогон Actions зелёный, пакет виден на `ghcr.io/divlv/exam700`.

**Кластер:** после применения — `k3s kubectl -n mywebs get pod,svc,ingress,pvc`, `logs deploy/az700` (видна строка RunId), `curl -I https://az700.v1.lv/healthz` возвращает 200 с валидным сертификатом.

---

## Риски

- **Память ноды.** Свободно ≈497Mi под requests. 128Mi влезают, но если другое приложение вырастет, pod встанет в `Pending`. Безопасный отказ: `low-priority` не вытесняет работающие сайты.
- **Первый сертификат** не выпустится без живой DNS-записи.
- **Пакет GHCR по умолчанию приватный** — забудете переключить, получите `ImagePullBackOff`.
- **Права на `/data/az700`** — если каталог создаст kubelet (root, 0755), контейнер от uid 1000 не запишет базу. Поэтому каталог создаётся вручную до первого применения.
- **`:latest` на одной ноде** — без `imagePullPolicy: Always` рестарт может молча не подтянуть новый образ. Политика выставлена явно.

## Вне объёма

OCR, многопользовательский режим, HA, пересборка датасета внутри кластера, изменения в `sample/` и `sample-k3s/`, правка ансибл-репозитория `myk3s` (в инструкции указано, куда положить файлы, если захотите закрепить их там).

## Первый шаг реализации

Создать `CONTINUITY.md` в корне по формату из `CLAUDE.md` и вести его дальше по ходу работы.
