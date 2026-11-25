<p align="center"><img src="assets/icon.svg" width="120" alt="Ráfaga"></p>

# Ráfaga — descargas rápidas de modelos para ComfyUI

**Ráfaga** baja los modelos de ComfyUI partiendo cada archivo en varios tramos que se descargan **en paralelo** (pedidos HTTP por rangos), **verifica el SHA-256** contra el que publica Hugging Face, **retoma** las descargas cortadas y deja el modelo en la carpeta correcta, listo para usar.

> *English below.*

## Por qué

Muchas conexiones a internet limitan la velocidad **por conexión**: el navegador o el gestor de ComfyUI bajan cada archivo por una sola conexión y se quedan en una fracción de lo que da la línea. En una prueba real con Qwen Image 2.1 (26,8 GB): **1 conexión ≈ 8 MB/s, 8 conexiones ≈ 16 MB/s**, la mitad del tiempo.

## Qué hace

- ⚡ **Descarga segmentada**: de 1 a 16 conexiones por archivo (8 por defecto).
- ✅ **Verificación SHA-256** automática cuando el servidor publica el hash (Hugging Face lo hace) o cuando el workflow lo declara. Si no coincide, el archivo se descarta.
- 🔁 **Reanudación**: si se corta la conexión o se cierra ComfyUI, retoma desde donde quedó.
- 🧩 **Modelos faltantes en un clic**: lee los modelos que declara el workflow abierto (los que muestra «Missing Models»), se fija cuáles faltan y los pone en cola.
- 📂 **Repos de Hugging Face**: pegás el link de un repo (o de una carpeta del repo), elegís qué archivos bajar y van todos a la cola, cada uno a su carpeta y con su SHA-256.
- 🔄 **Sin «Missing Models» fantasma**: al terminar refresca las listas de modelos y el índice de assets de ComfyUI.
- 🛡️ **Seguro**: solo escribe dentro de las carpetas de modelos, solo extensiones de modelo (`.safetensors`, `.ckpt`, `.gguf`…) y nunca sale de la carpeta elegida.
- 🌎 Interfaz en **español** e **inglés** (según el idioma de ComfyUI).

## Instalación

- **ComfyUI Manager / Registry**: buscá «Ráfaga» o `rafaga-downloader`, o con comfy-cli: `comfy node install rafaga-downloader`.
- **Manual**: cloná este repositorio dentro de `ComfyUI/custom_nodes/` y reiniciá ComfyUI.

No necesita dependencias extra: usa `aiohttp`, que ya viene con ComfyUI.

## Uso

1. Abrí el panel **Ráfaga** (ícono ⚡ en la barra lateral).
2. **Bajar modelos faltantes del workflow**: baja todo lo que el workflow abierto necesita y todavía no tenés.
3. **Descargar desde una URL**: pegá un enlace directo (por ejemplo `https://huggingface.co/<repo>/resolve/main/vae/modelo.safetensors`). La carpeta se deduce de la URL cuando el repo usa la estructura de ComfyUI; si no, elegila.
4. **Elegir archivos de un repo de Hugging Face**: pegá el link del repo (`https://huggingface.co/Comfy-Org/Qwen-Image`) o de una carpeta (`…/tree/main/split_files`). Ráfaga muestra solo los archivos de modelo, agrupados por carpeta de destino, con su tamaño y marcando los que ya tenés. Tildá los que quieras y tocá **Bajar seleccionados**. Si no se puede deducir la carpeta de algún archivo, te pide que la elijas.

> **No hace falta bajar todo el repo.** Un repo suele traer variantes del mismo modelo: precisiones (`bf16`, `fp8`, `nvfp4`), versiones `ema`/`nonema` o el mismo archivo en `.ckpt` y `.safetensors`. Para un workflow alcanza con uno de cada rol (modelo, text encoder y VAE) o con un solo checkpoint. Si tenés el workflow, el botón de modelos faltantes baja exactamente los que pide.

También está el comando **«Ráfaga: bajar modelos faltantes»** en el menú *Extensions → Ráfaga* y en la paleta de comandos. Las conexiones por archivo se ajustan en *Configuración → Ráfaga*.

### Modelos privados o con acceso restringido (gated)

Definí la variable de entorno `HF_TOKEN` antes de abrir ComfyUI. Ráfaga la envía **solo** a `huggingface.co`.

## Cómo funciona

1. Consulta el archivo (`HEAD`): tamaño, soporte de rangos y hash (`X-Linked-ETag` en Hugging Face).
2. Divide el archivo en tramos y los baja en paralelo sobre `<archivo>.rafaga.part`, guardando el avance en `<archivo>.rafaga.json`.
3. Verifica el tamaño y el SHA-256, y recién entonces renombra al nombre final.
4. Refresca las listas de modelos de ComfyUI.

API local (la usa la interfaz): `GET /rafaga/folders`, `GET /rafaga/jobs`, `POST /rafaga/jobs`, `POST /rafaga/jobs/{id}/cancel`, `POST /rafaga/jobs/clear`, `POST /rafaga/check`, `GET /rafaga/hf/files?url=<repo>`.

## Desarrollo

```bash
pip install aiohttp
python -m unittest discover -s tests -v
```

Las versiones y la publicación son **automáticas**. Cada push a `main` corre los tests y calcula la versión según el mensaje de commit ([Conventional Commits](https://www.conventionalcommits.org/es/)):

| Commit | Versión |
|---|---|
| `fix: …`, `perf: …`, `refactor: …` | 1.0.**1** |
| `feat: …` | 1.**1**.0 |
| `feat!: …` o `BREAKING CHANGE:` en el cuerpo | **2**.0.0 |
| `docs:`, `chore:`, `ci:`, `test:` | no publica |

Después actualiza `pyproject.toml`, crea la etiqueta `vX.Y.Z`, publica en el [Comfy Registry](https://registry.comfy.org) y crea el release en GitHub. El token del registry vive en el secret `REGISTRY_ACCESS_TOKEN` del repositorio.

---

# Ráfaga — fast model downloads for ComfyUI

**Ráfaga** downloads ComfyUI models by splitting each file into several HTTP range requests fetched **in parallel**, **verifies SHA-256** against the hash Hugging Face publishes, **resumes** interrupted downloads and drops the file into the right models folder.

- ⚡ 1–16 connections per file (default 8) · ✅ SHA-256 verification · 🔁 resume · 🧩 one-click download of the open workflow's missing models · 📂 pick files from a Hugging Face repo and queue them · 🔄 refreshes model lists and the asset index when done · 🛡️ writes only model files inside model folders.
- **Install**: search for "Ráfaga" / `rafaga-downloader` in ComfyUI Manager, `comfy node install rafaga-downloader`, or clone into `ComfyUI/custom_nodes/`.
- **Use**: open the ⚡ **Ráfaga** sidebar tab → *Download this workflow's missing models*, paste a direct file URL, or paste a Hugging Face repo/folder link (`https://huggingface.co/Comfy-Org/Qwen-Image`, `…/tree/main/split_files`) to choose which model files to download. A repo usually holds variants of the same model, so you rarely need all of it. Gated Hugging Face models: set `HF_TOKEN` (sent only to huggingface.co).
- Releases are automatic on every push to `main`, driven by Conventional Commits.

## License

[GPL-3.0](LICENSE), the same as ComfyUI.
