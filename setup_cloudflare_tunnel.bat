@echo off
REM === Cloudflare Tunnel Setup для GL_JB ===
REM Запустите от имени администратора

echo 1. Авторизация в Cloudflare
%USERPROFILE%\cloudflared.exe tunnel login

echo.
echo 2. Создание туннеля (замените my-tunnel на своё имя)
set /p TUNNEL_NAME="Имя туннеля [gl-jb]: "
if "%TUNNEL_NAME%"=="" set TUNNEL_NAME=gl-jb
%USERPROFILE%\cloudflared.exe tunnel create %TUNNEL_NAME%

echo.
echo 3. Настройка DNS (замените example.com на ваш домен)
set /p DOMAIN="Ваш домен (example.com): "
%USERPROFILE%\cloudflared.exe tunnel route dns %TUNNEL_NAME% %DOMAIN%

echo.
echo 4. Создание конфига
mkdir %USERPROFILE%\.cloudflared 2>nul
echo tunnel: %TUNNEL_NAME% > %USERPROFILE%\.cloudflared\config.yml
echo credentials-file: %USERPROFILE%\.cloudflared\%TUNNEL_NAME%.json >> %USERPROFILE%\.cloudflared\config.yml
echo. >> %USERPROFILE%\.cloudflared\config.yml
echo ingress: >> %USERPROFILE%\.cloudflared\config.yml
echo   - hostname: %DOMAIN% >> %USERPROFILE%\.cloudflared\config.yml
echo     service: http://localhost:5000 >> %USERPROFILE%\.cloudflared\config.yml
echo   - service: http_status:404 >> %USERPROFILE%\.cloudflared\config.yml

echo.
echo 5. Установка как сервиса (требует админа)
%USERPROFILE%\cloudflared.exe service install

echo.
echo Готово! Туннель запущен. Сайт доступен по https://%DOMAIN%
echo.
echo Полезные команды:
echo   cloudflared tunnel list
echo   cloudflared tunnel info %TUNNEL_NAME%
echo   net stop Cloudflared / net start Cloudflared
pause