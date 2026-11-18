# MECHAX GTA VI Widget

Widget OBS + servidor Flask para Kick. Incluye meta 0/30 y consulta de suscriptores activos.

## Render

Crear **Web Service** desde este repositorio, entorno Python:
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT app:app`
- Variables privadas: `PUBLIC_URL=https://TU-SERVICIO.onrender.com`, `KICK_CLIENT_ID`, `KICK_CLIENT_SECRET`, `ADMIN_KEY` (una cadena aleatoria larga).
- **Persistencia:** este ejemplo usa SQLite en `DATABASE_PATH` (por defecto `/tmp/mechax-widget.sqlite3`). En Render sin disco persistente, **se pierde el progreso y los tokens al reiniciar o desplegar**. Para usarlo en producción necesitas un disco persistente montado y configurar `DATABASE_PATH` a su ruta (por ejemplo `/var/data/widget.sqlite3`). Los discos persistentes requieren un plan compatible. No uses instancias múltiples.

## Kick

- Redirect URL: `https://TU-SERVICIO.onrender.com/auth/callback`
- Webhook URL: `https://TU-SERVICIO.onrender.com/webhooks/kick`
- Scopes: `user:read`, `channel:read`, `events:subscribe`.
- Tras desplegar, abrir `https://TU-SERVICIO.onrender.com/auth/start?key=TU_ADMIN_KEY` **solo en tu navegador**; no compartas esa URL.
- Overlay OBS: `https://TU-SERVICIO.onrender.com/overlay`
- Demo: `/overlay?demo=1`.

## Consideraciones

- Las renovaciones no cuentan. Las nuevas subs suman 1 y los regalos se suman por destinatarios reconocidos en el payload.
- La firma RSA de los webhooks se verifica contra la clave pública de Kick. No se procesan solicitudes sin firma válida.
- El servidor registra automáticamente eventos al autorizar Kick; confirma en los logs que se registraron.
- El total de suscriptores activos depende del campo `active_subscribers_count` de la API de Kick.
- Si el formato real de eventos de regalos difiere de `giftees`/`gifted_users`/`gift_count`, habrá que adaptar ese campo antes de usarlo para sorteos reales.
- El widget señala que se alcanzó la meta; **no elige ganadores ni entrega premios automáticamente**.
- No guardes secretos en GitHub. Cambia el `ADMIN_KEY` si lo compartes por error.
