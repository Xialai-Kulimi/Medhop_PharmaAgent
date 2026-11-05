"""Scripted stand-in for the model, for validation scripts.

The test decides which tools the "model" calls, so the real agent loop, guards and
tools run without a model server. Patients are created the same way the agent does
it (create_patient, update_history, add_medications); nothing parses free text.
"""
import json
import time

import pharmacist_agent
import record_tools


def wait(client, path, limit=120):
    end = time.time() + limit
    while True:
        response = client.get(path)
        assert response.status_code == 200, response.text
        data = response.json()
        if data.get('status') != 'running':
            return data
        assert time.time() < end, 'timed out: ' + path
        time.sleep(.05)


def ask(client, message, calls=(), reply='已處理。'):
    """Send one pharmacist message. The scripted model issues `calls` [(tool, args), ...] in one turn, then replies.

    The returned run snapshot also carries `results` (what each tool returned to the model) and, for a
    started review, the finished job under the action's `job_result`.
    """
    plan = []
    if calls:
        plan.append({'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': f'call-{i}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}
            for i, (name, args) in enumerate(calls)]})
    plan.append({'role': 'assistant', 'content': reply})
    seen = {'tools': [], 'messages': []}

    def model(cfg, messages, first):
        seen['tools'] = [m for m in messages if m['role'] == 'tool']
        seen['messages'] = list(messages)
        return plan.pop(0) if plan else {'role': 'assistant', 'content': reply}

    pharmacist_agent.call_model = model
    response = client.post('/api/agent', json={'message': message})
    assert response.status_code == 200, response.text
    run = wait(client, '/api/agent/' + response.json()['id'])
    run['results'] = []
    for item in seen['tools']:
        try:
            run['results'].append(json.loads(item['content']))
        except ValueError:
            run['results'].append({'raw': item['content']})
    run['model_messages'] = seen['messages']
    for action in run['actions']:
        if action['type'] in ('review', 'quick_check'):
            action['job_result'] = wait(client, '/api/jobs/' + action['job'])
    return run


def med_rows(meds):
    rows = []
    for med in meds:
        if isinstance(med, dict):
            rows.append(med)
            continue
        med = (med,) if isinstance(med, str) else tuple(med)
        rows.append({k: v for k, v in zip(('name', 'dose', 'frequency', 'kind'), med) if v})
    return rows


def seed(client, name, meds=(), history=(), allergies=(), review=False, **profile):
    """A new patient, entered the way the agent enters one."""
    client.post('/api/patients/reset', json={})
    meds = med_rows(meds)
    calls = [('create_patient', {'name': name, **profile})]
    if history or allergies:
        calls.append(('update_history', {'add_history': list(history), 'add_allergies': list(allergies)}))
    if meds:
        calls.append(('add_medications', {'medications': meds}))
    if review:
        calls.append(('run_medication_review', {}))
    run = ask(client, '新病人' + name + '，' + '、'.join(m['name'] for m in meds), calls)
    failed = [s for s in run['steps'] if s['state'] == 'error']
    assert not failed, failed
    return run


def stored(store, session, name, meds=(), history=(), **profile):
    """The same structured writes against a bare store, for store-level checks."""
    store.create_patient(session, name, profile)
    if history:
        record_tools.edit_patient(store, session, {'history': list(history)})
    for med in med_rows(meds):
        record_tools.save_medication(store, session, None, med['name'], med.get('dose', ''), med.get('frequency', ''), med.get('kind', 'current'))
    return store.active(session)
