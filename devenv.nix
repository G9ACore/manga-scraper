{ pkgs, ... }:

{
  # Базовые пакеты
  packages = [
    pkgs.git
    pkgs.curl
    pkgs.nodejs
    pkgs.playwright-driver
    pkgs.playwright-driver.browsers

    # Зависимости для Playwright браузеров
    pkgs.ffmpeg
    pkgs.libva
    pkgs.libwebp

    (pkgs.python3.withPackages (ps: [
      ps.playwright
      ps.playwright-stealth
    ]))
  ];

  # Python с виртуальным окружением
  languages.python = {
    enable = true;
    package = pkgs.python314;
    venv = {
      enable = true;
      requirements = builtins.readFile ./requirements.txt;
    };
  };

  # Переменные окружения (если понадобятся)
  env = {
    MANGA_OUTPUT_DIR = "~/Downloads/";

    # 2. Указываем, где лежат рабочие браузеры
    PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1";
  };

  enterShell = ''
    echo "🗡️  Scraper ready. Python: $(python --version)"
    echo "⚠️  First time? Run: playwright install chromium"
  '';
}
