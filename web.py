from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response
import io, csv, json
from .transform import read_csv, build_rows
from .pricing import simulate, DEFAULT_CONFIG
from .db import save_snapshot, get_snapshot, list_snapshots, save_scenario

app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=25*1024*1024

@app.get('/')
def index(): return render_template('index.html', default_config=DEFAULT_CONFIG)

@app.get('/api/snapshots')
def snapshots(): return jsonify(list_snapshots())

@app.post('/api/upload')
def upload():
    try:
        dfs={k:read_csv(request.files[k],k) for k in ['accounts','contracts','customers','subscriptions']}
        rows,validation=build_rows(dfs['accounts'],dfs['contracts'],dfs['customers'],dfs['subscriptions'])
        sid=save_snapshot(request.form.get('name','Uploaded pricing snapshot'),validation['analysis_date'],rows,validation)
        return jsonify({'snapshot_id':sid,'validation':validation})
    except Exception as e: return jsonify({'error':str(e)}),400

@app.post('/api/simulate')
def api_simulate():
    body=request.get_json(force=True); snap=get_snapshot(int(body['snapshot_id']))
    if not snap: return jsonify({'error':'Snapshot not found'}),404
    result=simulate(snap['rows'],body.get('config'),snap['analysis_date'],body.get('horizon_months',12))
    if body.get('save'):
        result['scenario_id']=save_scenario(snap['id'],body.get('scenario_name','Scenario'),result['config'],result['summary'])
    return jsonify(result)

@app.post('/api/export')
def export():
    body=request.get_json(force=True); snap=get_snapshot(int(body['snapshot_id']))
    result=simulate(snap['rows'],body.get('config'),snap['analysis_date'],body.get('horizon_months',12))
    segment=body.get('segment','All'); rows=result['rows'] if segment=='All' else [r for r in result['rows'] if r['segment']==segment]
    fields=['account_name','email','csm','ae','product','quantity','unit_price','list_price','floor_price','proposed_unit_price','price_change_pct','discount_from_list_pct','arr_change','contract_status','contract_end_date','effective_date','tenure_years','segment','strategic_legacy','below_floor_before','above_list_after']
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows([{k:r.get(k) for k in fields} for r in rows])
    return Response(s.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename=pricing_{segment.lower().replace(" ","_")}.csv'})
