import re,html,sys
t=open(sys.argv[1],errors='ignore').read()
t=re.sub(r'<script.*?</script>|<style.*?</style>','',t,flags=re.S)
t=re.sub(r'<br\s*/?>|</p>|</li>|</tr>|</div>|</h\d>','\n',t)
t=html.unescape(re.sub(r'<[^>]+>',' ',t))
t=re.sub(r'[ \t]+',' ',t); t=re.sub(r'\n\s*\n+','\n',t)
print(t)
