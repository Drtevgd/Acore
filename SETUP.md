# Настройка Discord Proxy Bot

## Схема работы
```
Rust Server (RU) → HTTP → Python Bot (VPS за рубежом) → Discord API
```

## 1. Настройка Python-бота (на VPS вне России)

### Установка
```bash
pip install discord.py aiohttp
```

### Конфиг в discord_proxy_bot.py
- `API_SECRET` — придумай любой секретный ключ (например `"xK9mP2qR7vL"`)
- `RUST_SERVER_URL` — IP твоего Rust-сервера, порт 8766

### Запуск
```bash
python discord_proxy_bot.py
```

Для автозапуска через systemd:
```ini
[Unit]
Description=Discord Proxy Bot

[Service]
ExecStart=/usr/bin/python3 /path/to/discord_proxy_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

## 2. Патч плагина ACore.cs

### Что убрать
- Все `using Oxide.Ext.Discord.*`
- Поля `[DiscordClient] DiscordClient Client`, `_discordSettings`, `_guild`
- Методы: `OnDiscordGatewayReady`, `RegisterDiscordCommands`, `OnDiscordInteractionCreated`, `HasDiscordPermission`, `RespondToInteraction`, `CreateScreenshotButtons`

### Что добавить
Скопируй из `ACore_patch.cs`:
- Константы `ProxyUrl` и `ProxySecret` (вместо `DiscordBotToken`)
- Все методы из патча

### Замени в Unload()
```csharp
private void Unload()
{
    _httpListener?.Stop();
    _httpListener?.Close();
    _httpListener = null;
}
```

## 3. Открой порты
- На VPS: порт `8765` (входящий, от Rust-сервера)
- На Rust-сервере: порт `8766` (входящий, от VPS)

## 4. Проверь
1. Запусти Python-бот на VPS
2. Загрузи патченный плагин на сервер
3. В консоли Rust должно появиться: `Discord proxy mode: отправка через Python-бот`
4. Протестируй: `screenshot <steamid>` в консоли сервера
