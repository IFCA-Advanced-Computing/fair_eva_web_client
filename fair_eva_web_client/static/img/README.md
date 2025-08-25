# FAIR EVA Web Client – base moderna (cabecera, footer e índice)

Este paquete contiene una base de plantillas modernas (Bootstrap 5) respetando menús, logos y pie de página,
además de **un tema de color** con variables CSS ajustadas a los colores originales (configurables en `static/css/theme.css`).

## Qué incluye
- `templates/base_modern.html`: layout con cabecera (menú + banner) y pie.
- `templates/snippets/footer.html`: pie con los logos originales.
- `templates/index.html`: portada para introducir el ID y seleccionar plugin activo.
- `static/css/theme.css`: **tema de color** con variables (`--eva-*`) para adaptar a tu branding.
- `static/js/custom.js`: punto de entrada para scripts ligeros de UI.

## Cómo integrarlo
1. Copia el contenido de `fair_eva_web_client/templates` y `fair_eva_web_client/static` en tu paquete.
2. Cambia tus vistas para renderizar `index.html` (que hereda de `base_modern.html`).
3. Ajusta `config.TITLE` y `config.LOGO_URL` en tu app Flask, y coloca tus logos en `static/img/`:
   - `logo.png` (cabecera)
   - `logo_fair_eosc_2.png` (banner)
   - `csic.png`, `red.png`, `dc_logo.png`, `ifca.png` (footer)

## Personalización de colores
Edita `static/css/theme.css` y ajusta las variables:
```css
:root {
  --eva-primary:  #c60c30; /* rojo base */
  --eva-secondary:#004b80; /* azul */
  --eva-accent:   #0097d7; /* acento */
  --eva-success:  #2ecc71; /* verde */
  --eva-warning:  #f1c40f; /* amarillo */
  --eva-danger:   #e74c3c; /* rojo alertas */
}
```
