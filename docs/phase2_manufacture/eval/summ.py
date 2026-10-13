import json,sys
d=json.load(open(sys.argv[1])); 
for name,m in d['models'].items():
    print('###',name,'effort',d['effort'])
    for a in m['A']: print(' A pass %d/%d sec avg %s max %s tok %s'%(a['pass'],a['of'],a['secAvg'],a['secMax'],a['outTokAvg']), a['issues'])
    for b in m['B']: print(' B ok',b['ok'],'n',b['n'],'sec',b['sec'],b['issues'][:5])
    for c in m['C']: print(' C',c.get('acc'),c.get('wrong'))
    print(' nameStability',m.get('nameStability'),'baselineC',d['baselineC']['acc'])
