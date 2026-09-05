from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.exceptions import HTTPException
import hmac, io, csv, logging, os
from .transform import read_csv, build_rows
from .pricing import simulate, sensitivity_grid, price_distribution, DEFAULT_CONFIG
from .db import save_snapshot, get_snapshot, list_snapshots, save_scenario

app=Flask(__name__)
app.config['MAX_CONTENT_LENGTH']=25*1024*1024
app.logger.setLevel(logging.INFO)

APP_PASSWORD = os.environ.get("APP_PASSWORD")
GENERIC_ERROR = "An unexpected error occurred. Please try again or contact support."
CSV_INJECTION_PREFIXES = ("=","+","-","@","\t","\r")

@app.before_request
def require_auth():
    if not APP_PASSWORD or request.path == '/healthz':
        return None
    auth = request.authorization
    if not auth or not hmac.compare_digest(auth.password or '', APP_PASSWORD):
        return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Pricing Tool"'})

@app.errorhandler(Exception)
def handle_unexpected_error(e):
    # Routing/abort errors carry their own status (404, 405, 413 ...) and must
    # keep it; only genuinely unexpected failures become an opaque 500.
    if isinstance(e, HTTPException): return e
    app.logger.exception("Unhandled exception while handling %s %s", request.method, request.path)
    return jsonify({'error': GENERIC_ERROR}), 500

def sanitize_csv_value(v):
    if isinstance(v, str) and v.startswith(CSV_INJECTION_PREFIXES):
        return "'" + v
    return v

@app.get('/healthz')
def healthz(): return jsonify({'status': 'ok'})

@app.get('/')
def index(): return render_template('index.html', default_config=DEFAULT_CONFIG)

@app.get('/api/snapshots')
def snapshots(): return jsonify(list_snapshots())

@app.post('/api/upload')
def upload():
    try:
        dfs={k:read_csv(request.files[k],k) for k in ['accounts','contracts','customers','subscriptions']}
        # An explicit analysis date pins a reproducible run; omitted, it falls
        # back to the latest billing date in the uploaded customer file.
        rows,validation=build_rows(dfs['accounts'],dfs['contracts'],dfs['customers'],dfs['subscriptions'],
                                    analysis_date=request.form.get('analysis_date') or None)
    except ValueError as e:
        return jsonify({'error':str(e)}),400
    except KeyError as e:
        return jsonify({'error':f'Missing upload file: {e}'}),400
    sid=save_snapshot(request.form.get('name','Uploaded pricing snapshot'),validation['analysis_date'],rows,validation)
    return jsonify({'snapshot_id':sid,'validation':validation})

@app.post('/api/simulate')
def api_simulate():
    body=request.get_json(force=True); snap=get_snapshot(int(body['snapshot_id']))
    if not snap: return jsonify({'error':'Snapshot not found'}),404
    result=simulate(snap['rows'],body.get('config'),snap['analysis_date'],body.get('horizon_months',12))
    if body.get('save'):
        result['scenario_id']=save_scenario(snap['id'],body.get('scenario_name','Scenario'),result['config'],result['summary'])
    return jsonify(result)

@app.post('/api/sensitivity')
def api_sensitivity():
    body=request.get_json(force=True); snap=get_snapshot(int(body['snapshot_id']))
    if not snap: return jsonify({'error':'Snapshot not found'}),404
    grid=sensitivity_grid(snap['rows'],body.get('config'),snap['analysis_date'],body.get('horizon_months',12),
                           body.get('increase_options'),body.get('churn_options'))
    return jsonify({'grid':grid})

@app.post('/api/distribution')
def api_distribution():
    body=request.get_json(force=True); snap=get_snapshot(int(body['snapshot_id']))
    if not snap: return jsonify({'error':'Snapshot not found'}),404
    result=simulate(snap['rows'],body.get('config'),snap['analysis_date'],body.get('horizon_months',12))
    return jsonify({'distribution':price_distribution(result)})

@app.post('/api/export')
def export():
    body=request.get_json(force=True); snap=get_snapshot(int(body['snapshot_id']))
    if not snap: return jsonify({'error':'Snapshot not found'}),404
    result=simulate(snap['rows'],body.get('config'),snap['analysis_date'],body.get('horizon_months',12))
    segment=body.get('segment','All'); rows=result['rows'] if segment=='All' else [r for r in result['rows'] if r['segment']==segment]
    include_contact=bool(body.get('include_contact',False))
    # Identifiers first so the export can be joined line-by-line against an
    # external model, then the derived inputs, then the pricing outputs.
    fields=['subscription_item_id','stripe_customer_id','salesforce_id','account_key','match_method',
            'account_name','csm','ae','product','billing_interval','quantity',
            'unit_price','current_mrr','current_arr','tenure_years','last_billing_date',
            'contract_status','contract_end_date','next_eligible_date','effective_date','realized_in_horizon',
            'list_price','floor_price','proposed_unit_price','price_change_pct',
            'discount_from_list_pct_before','discount_from_list_pct','arr_change','realized_revenue_in_horizon',
            'segment','strategic_legacy','floor_binds','below_floor_before','below_floor_after','above_list_after','held_reason']
    if include_contact: fields.insert(1,'email')
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader()
    w.writerows([{k:sanitize_csv_value(r.get(k)) for k in fields} for r in rows])
    return Response(s.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename=pricing_{segment.lower().replace(" ","_")}.csv'})
