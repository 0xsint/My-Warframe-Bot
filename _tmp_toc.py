import re
text = open(r'C:\Users\Admin\AppData\Local\Temp\1791555474183-copilot-tool-output-8576a0c625de4862b74d8d2318d65e84.txt', encoding='utf-8').read()
matches = re.findall(r'toctext">([^<]+)</span>', text)
print(matches)
