def get_flight_phase(result, prev_result=None):
    """
    Determine the phase of flight for a given result dict.
    Phases: 'Liftoff', 'Powered Ascent', 'Coast', 'Apogee', 'Descent', 'Chute Descent', 'Landed'
    """
    if result['altitude'] <= 0 and result['velocity'] == 0:
        return 'Landed'
    if result['time'] == 0:
        return 'Liftoff'
    if result.get('chute_deployed', False):
        return 'Chute Descent'
    if prev_result and prev_result['altitude'] < result['altitude'] and result['thrust'] > 1:
        return 'Powered Ascent'
    if prev_result and prev_result['altitude'] < result['altitude'] and result['thrust'] <= 1:
        return 'Coast'
    if prev_result and prev_result['altitude'] > result['altitude']:
        if result.get('chute_deployed', False):
            return 'Chute Descent'
        return 'Descent'
    return 'Coast'