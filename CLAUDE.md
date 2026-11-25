# Ráfaga (rafaga-downloader) — contexto para desarrollo

Extensión de ComfyUI que descarga modelos partiendo cada archivo en tramos HTTP por rangos en paralelo (1 a 16 conexiones, 8 por defecto), verifica el SHA-256, reanuda descargas cortadas y baja con un clic los modelos que le faltan al workflow abierto. Al terminar refresca las listas de modelos y el índice de assets de ComfyUI. No agrega nodos al lienzo.

- Repo: https://github.com/ElkronAiken/rafaga-downloader · rama `main` · licencia GPL-3.0
- Comfy Registry: id `rafaga-downloader`, publisher `elkronaiken`
- Idioma del proyecto: español (README bilingüe, español primero). Mensajes de commit en español.

## Estructura

| Archivo | Rol |
|---|---|
| `__init__.py` | Registra las rutas (importa `rafaga.server`), `WEB_DIRECTORY = "./web"`, `NODE_CLASS_MAPPINGS = {}` |
| `rafaga/downloader.py` | Núcleo **sin imports de ComfyUI**: `probe()` (HEAD con fallback a GET por rango), `plan_segments()`, `SegmentedDownload` (archivo `.rafaga.part` pre-asignado, estado de reanudación en `.rafaga.json`, buffer de 4 MB que se guarda aunque la conexión se corte, reintentos con backoff, SHA-256 en un thread, renombre atómico al final) y `discard()` al cancelar |
| `rafaga/server.py` | Integración con ComfyUI: `GET /rafaga/folders`, `GET/POST /rafaga/jobs`, `POST /rafaga/jobs/{id}/cancel`, `POST /rafaga/jobs/clear`, `POST /rafaga/check` y `GET /rafaga/hf/files?url=`. Cola `JobManager` (`MAX_PARALLEL_FILES = 1`). `resolve_destination()` valida carpeta conocida, sin `..`, extensiones de modelo (`supported_pt_extensions` más `.gguf` y `.onnx`) y destino dentro de la carpeta. `default_dir()` prefiere el directorio con el nombre del tipo (Desktop registra primero `unet`, `clip` y `t2i_adapter`), y `JobManager.add()` marca «exists» si el archivo ya está en cualquier directorio del tipo (`installed_path()`). `guess_folder()`/`folder_from_segments()` deducen la carpeta desde rutas de Hugging Face. `parse_hf_repo_url()` + `list_hf_repo()` listan un repo con la API `tree` (paginación solo en el mismo host; SHA-256 de `lfs.oid`; detecta repos diffusers por `model_index.json`). `HF_TOKEN` solo se envía a huggingface.co. Al terminar, `refresh_model_lists()` y `asset_seeder.start(roots=("models",))` con reintentos. Eventos `rafaga.progress` vía `PromptServer.send_sync` |
| `web/rafaga.js` | Interfaz: panel lateral (`app.extensionManager.sidebarTab.registerSidebarTab`, id `rafaga`), comando `Rafaga.DownloadMissing` (menú *Extensions → Ráfaga*), ajuste `Rafaga.Connections` (1 a 16), textos es/en según `Comfy.Locale`, tamaños en unidades decimales (como Hugging Face). Los modelos faltantes salen de `app.graphToPrompt().workflow`: `nodes[].properties.models`, `definitions.subgraphs[].nodes` y `workflow.models`. Un link a un repo de HF (o a `…/tree/<rev>/<carpeta>`) abre el explorador: archivos agrupados por carpeta sugerida, nada tildado por defecto, carpeta obligatoria si no se deduce. Al completar ejecuta `Comfy.RefreshNodeDefinitions` |
| `tests/` | `unittest`: servidor aiohttp local para el núcleo y stand-ins de `folder_paths`/`server` para las validaciones. No necesitan red ni ComfyUI |
| `.github/workflows/release.yml` | Release automático (ver abajo). `tests.yml` corre en los PR |
| `.github/scripts/next_version.py` | Calcula la versión desde los commits (recibe `git log` por stdin; sin `subprocess`) |
| `.comfyignore` | Lo que no viaja en el paquete del registry (`.github/`, `tests/`, `CLAUDE*.md`, etc.) |

## Tests

```bash
python -m unittest discover -s tests -v   # desde la raíz; requiere aiohttp
```

Cada bug que se arregle va con un test que lo reproduzca, cuando sea posible. Mantener los tests de seguridad de `resolve_destination()`.

## Release automático (leer antes de cada commit)

Cada push a `main` corre los tests y **puede publicar una versión en el Comfy Registry**. El tipo de commit (Conventional Commits) decide:

| Commit | Resultado |
|---|---|
| `fix:` `perf:` `refactor:` `revert:` `deps:` | patch (1.0.**1**) |
| `feat:` | minor (1.**1**.0) |
| `tipo!:` o `BREAKING CHANGE:` en el cuerpo | major (**2**.0.0) |
| `docs:` `chore:` `ci:` `test:` `style:` `build:` | no publica |

El workflow escribe la versión en `pyproject.toml`, publica con `Comfy-Org/publish-node-action@main` (secret `REGISTRY_ACCESS_TOKEN`; con `skip_checkout: 'true'`, porque el checkout propio de la action hace `git reset --hard` y borra la versión escrita), después commitea `chore(release): vX.Y.Z [skip ci]`, crea la etiqueta `vX.Y.Z` y el GitHub release. Si la publicación falla, no se crea la etiqueta y se puede reintentar.

## Reglas

1. **No hacer push sin aprobación explícita del mantenedor.** Antes, mostrar el diff resumido, el mensaje de commit y la versión que va a salir.
2. Tampoco crear issues, comentarios ni publicaciones en GitHub sin aprobación.
3. Estándares del registry: nada de `eval`, `exec`, `subprocess` ni instalación de paquetes en runtime, y nada de código ofuscado.
4. Sin dependencias nuevas: usar `aiohttp` (ya viene con ComfyUI) y la librería estándar.
5. Nunca pedir ni manipular el token del registry.
6. Para APIs del frontend de ComfyUI, consultar los source maps del paquete `comfyui_frontend_package` instalado en vez de adivinar. La severidad `warning` de los toasts está deprecada: usar `warn`.
7. Cambios en Python requieren reiniciar ComfyUI. Cambios en JS se ven al recargar la interfaz.

## Ideas no implementadas (no hacerlas sin pedido)

Soporte de Civitai con token, pausar/reanudar en la interfaz, conexiones por descarga, varios archivos en paralelo, integración con el diálogo nativo de «Missing Models», más idiomas.
