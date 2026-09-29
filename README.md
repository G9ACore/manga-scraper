# Manga Parser (com-x.life)
A script to parse and download manga from com-x.life.

## How to run (source code)
1. Install dependencies from `requirements.txt`
2. Run `python main.py`. It will display a list of available options
3. Copy the link to the main page of the desired manga from the site and pass it to the script as an argument
4. Wait for the download to finish.

### Remark
If an error occurs while downloading any chapters, the script will display a list of them upon completion. You must then manually restart the script, which will skip already downloaded chapters by detecting them in the directory specified in `config.py`

## TODO
- Add a verbose option to avoid outputting unnecessary information 
