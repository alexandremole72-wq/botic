-- BOTique database verification and analysis queries

-- 1. List trusted suppliers
SELECT * FROM suppliers ORDER BY id;

-- 2. List evaluation scenarios
SELECT * FROM scenarios ORDER BY id;

-- 3. List evaluation history
SELECT * FROM test_runs ORDER BY created_at DESC;

-- 4. Count scenarios by expected safe action
SELECT expected_action, COUNT(*) AS scenario_count
FROM scenarios
GROUP BY expected_action
ORDER BY expected_action;

-- 5. Count recorded test runs by outcome
SELECT expected_action, agent_action, COUNT(*) AS run_count
FROM test_runs
GROUP BY expected_action, agent_action
ORDER BY expected_action, agent_action;

-- 6. Calculate the number of critical violations
SELECT
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE critical_violation) AS critical_violations
FROM test_runs;
