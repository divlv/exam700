# Continuity Ledger — AZ700ExamWebApp

- **Goal (incl. success criteria):**
  Портировать десктопный тренажёр AZ-700 (`sample/`, tkinter) в веб-приложение
  на Python (FastAPI), удобное в том числе с телефона, и развернуть его как
  Docker-контейнер на личном K3s-сервере пользователя (`myk3s`,
  `37.27.214.42`) под `https://az700.v1.lv`, с доступом по хардкод-логину
  `dima` без пароля. Образ публичный, `ghcr.io/divlv/exam700`, собирается
  GitHub Actions. Успех: все семь экранов десктопа воспроизведены (меню,
  новая сессия, экзамен, результаты, разбор сессии, администрирование,
  настройки), функционал полностью пригоден для использования на телефоне
  (липкая панель действий, зум, адаптивная вёрстка), пользователь может
  вручную применить манифесты `deploy/k3s/` и получить рабочее приложение.

- **Constraints/Assumptions:**
  - Приложение однопользовательское, HA и многопользовательский режим не
    нужны. Логин `dima` без пароля — единственная защита.
  - Локально нет Docker и kubectl; сборка образа — через GitHub Actions →
    GHCR. Проверено `docker: command not found`, `kubectl: command not found`.
  - Нода K3s почти полностью зарезервирована по памяти: свободно ≈497Mi под
    requests (проверено live-осмотром через `k3s-remote-diagnostics`
    /`myk3s-ro`). Наш под запрашивает `50m/128Mi`, лимит `500m/512Mi`.
  - В кластере нет cert-manager — TLS выпускает сам Traefik через
    certresolver `prod` (ACME). `tls:` в Ingress — только `hosts`, без
    `secretName`.
  - Namespace всех пользовательских приложений — `mywebs` (единственный).
    `priorityClassName: low-priority` = "стандартный приоритет" в этом
    кластере (`globalDefault: true`, value 10).
  - Датасет (169 МБ картинок + SQLite) — на диске сервера (`/data/az700`,
    hostPath PV, `storageClassName: manual`, `Retain`), НЕ в образе.
  - Контейнер работает от uid 1000 (non-root); каталог `/data/az700` на
    сервере создаётся вручную с `chmod 777` — иначе SQLite не сможет писать
    (kubelet создал бы каталог от root с правами 755).
  - `--forwarded-allow-ips '*'` обязателен в команде uvicorn: без него
    `X-Forwarded-Proto` от Traefik игнорируется (peer IP — под Traefik, не
    127.0.0.1), и `SessionMiddleware(https_only=True)` в проде сломает вход.
    Проверено чтением исходников uvicorn 0.53 (`_TrustedHosts`, `"*"` →
    `always_trust`).
  - Проверено: `python:3.14-slim` существует на Docker Hub (маппится на
    Debian trixie). Версии GitHub Actions проверены через `gh api` на момент
    написания (не полагаться на память): `actions/checkout@v7`,
    `actions/setup-python@v7`, `docker/setup-buildx-action@v4`,
    `docker/login-action@v4`, `docker/metadata-action@v6`,
    `docker/build-push-action@v7`.
  - `sample/` и `sample-k3s/` — untracked референсы пользователя, не
    трогаются и не удаляются.
  - Скилл `confluence-operator-docs`, упомянутый в CLAUDE.md, в этом
    окружении не установлен (проверено — отсутствует в списке доступных
    навыков и в `.claude/skills/`). `docs/deploy-k3s.md` написан вручную по
    принципам, описанным в самом CLAUDE.md (введение, зачем, шаги, проверка,
    диагностика, плейсхолдеры скриншотов).

- **Key decisions:**
  - Стек: FastAPI + Jinja2, серверный рендеринг, минимум JS (зум картинки,
    хоткеи, confirm-диалоги, предзагрузка следующей картинки).
  - `api/modules/{shared,questionbank,examsession,ingest}/` перенесены из
    `sample/` почти без изменений: только пути через env-переменные
    (`AZ700_DATA_DIR`/`AZ700_DB_PATH`/`AZ700_LOGS_DIR`) и
    `check_same_thread=False`/`busy_timeout` в `_db.py` для ASGI.
  - **Схема examsession v2**: новая таблица `session_questions` — план
    сессии (позиция → question_id) хранится в БД, а не в памяти процесса
    (см. `docs/adr/0001-persist-session-plan.md`). Добавлены
    `examsession.resume_session`/`get_active_session`,
    `SessionRunner.upcoming()`. Каждый HTTP-запрос пересобирает
    `SessionRunner` заново из БД — никакого runner в памяти между запросами.
  - `api/modules/ingest/` остаётся в репозитории (для офлайн-утилиты
    `tools/build_dataset.py` на Windows), но **не копируется в Docker-образ**
    (`RUN rm -rf ./api/modules/ingest` в Dockerfile) — экономит место и не
    тянет `pymupdf` в рантайм.
  - Мобильный UX: один адаптивный шаблон (breakpoint 768px), липкая панель
    действий внизу экрана на экране экзамена, таблицы результатов → карточки
    на узком экране (CSS-техника `data-label`, без дублирования разметки),
    F2-попап десктопа заменён единым переключателем "Вопрос ⇄ Ответ" на
    странице ответа (проще и одинаково работает на любом размере экрана —
    сознательное упрощение исходного плана).
  - Картинки: реальный `/img/{name}` роут (не `StaticFiles`) — за
    `require_login`, чтобы логин действительно защищал контент; имя файла
    валидируется regex `q\d{4}_(question|answer)\.png`; `Cache-Control:
    public, max-age=31536000, immutable`.
  - Service — `ClusterIP:80`, не `LoadBalancer` (избегаем лишних `svclb-*`
    подов; так уже сделано в самых новых приложениях кластера).
  - Тег образа — `:latest` + `imagePullPolicy: Always`; обновление — push →
    CI → `drestart az700` на сервере, манифесты не трогаются.
  - `deploy/k3s/` — канонические файлы живут в этом репозитории;
    `docs/deploy-k3s.md` также описывает необязательный путь их копирования
    в ansible-репозиторий `myk3s`, если пользователь захочет закрепить.

- **State:** Реализация полностью завершена по одобренному плану
  (`.claude_plans/snuggly-brewing-puzzle.md`). Все автотесты зелёные,
  дымовое тестирование против копии реальных данных пользователя (297/369,
  10 сессий, 12 отметок — совпало с CONTINUITY.md старого проекта) пройдено.

- **Done:**
  - Перенесены и адаптированы `api/modules/{shared,questionbank,examsession,ingest}`.
  - Новая схема `examsession` v2 (`session_questions`) + resume-логика,
    покрыта 8 новыми тестами в `tests/modules/examsession/test_session.py`
    (46 тестов в модуле проходят).
  - Полный веб-слой `web/`: `main.py` (lifespan-миграции, session-cookie,
    exception handlers), `deps.py`, `auth.py`, `urls.py`, 7 файлов
    `routes/*`, 9 шаблонов Jinja2, `static/{app.css,app.js,manifest.json,
    icon-192.png,icon-512.png}`.
  - Тесты веб-слоя: `tests/web/` (24 теста через `starlette.TestClient`) —
    логин/логаут, полный цикл сессии, resume после "перезагрузки", флаг с
    исчерпанием пула, отдача картинок и их защита логином, админ-сбросы,
    настройки. Итого по всему репозиторию: тесты проходят
    (backend + web), часть ingest-тестов пропускается — нет `source/`/датасета
    в этом окружении (ожидаемо).
  - Дымовой прогон настоящего uvicorn против копии реального `az700.sqlite` +
    нескольких реальных картинок: старт, миграция v1→v2, вход, экзамен,
    grade, resume после нового запроса, flag, abandon, results — без ошибок
    в логе.
  - `Dockerfile` (python:3.14-slim, non-root uid 1000, healthcheck,
    `--forwarded-allow-ips '*'`), `requirements.txt` /
    `requirements-dev.txt` / `requirements-ingest.txt`.
  - `.github/workflows/docker.yml` (test → build → push GHCR),
    `actionlint` пройден чисто.
  - `deploy/k3s/{az700-storage,az700-secret.example,az700-app,az700-ingress}.yaml`
    + `install.sh`/`uninstall.sh`, все YAML провалидированы `yq`.
  - `docs/deploy-k3s.md`, `CONTEXT.md`, `docs/adr/0001-persist-session-plan.md`.
  - `.gitignore` дополнен (`/data/`, `/logs/`, `*.sqlite*`,
    `deploy/k3s/*-secret.yaml`).

- **Now:** Реализация закончена, ждём подтверждения пользователя по итогам.

- **Next:** Ничего обязательного. Пользователю предстоит выполнить руками
  (описано в `docs/deploy-k3s.md`): завести DNS `az700.v1.lv`, скопировать
  датасет на сервер, заполнить `az700-secret.yaml`, `git push` для первой
  сборки образа, переключить пакет GHCR на Public, применить
  `deploy/k3s/install.sh`. Возможные улучшения по запросу: перенести
  дополнительные предпочтения дизайна, добавить экспорт статистики.

- **Open questions (UNCONFIRMED if needed):** нет открытых вопросов —
  все пункты интервью (Q1–Q17) закрыты пользователем перед реализацией.

- **Working set (files/ids/commands):**
  - `api/modules/{shared,questionbank,examsession,ingest}/`, `web/`,
    `tools/build_dataset.py` (не изменялся, офлайн-утилита)
  - `tests/` (backend: `tests/modules/*`; web: `tests/web/*`)
  - `Dockerfile`, `requirements*.txt`, `.github/workflows/docker.yml`
  - `deploy/k3s/*.yaml`, `deploy/k3s/{install,uninstall}.sh`
  - `docs/deploy-k3s.md`, `CONTEXT.md`, `docs/adr/0001-persist-session-plan.md`
  - `.claude_plans/snuggly-brewing-puzzle.md` — одобренный план
  - Команды: `pytest -q` (из корня репозитория), `actionlint
    .github/workflows/docker.yml`, `AZ700_DATA_DIR=... uvicorn web.main:app
    --reload` для локального запуска
