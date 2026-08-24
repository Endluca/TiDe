BEGIN;

-- Forward-only business-content migration. Reverting the document definition
-- or course launch could make existing assignment/progress facts ambiguous.
DO $$
BEGIN
    RAISE NOTICE
        '0043_p_rel_execution_catalog is forward-only; no catalog, assignment or progress rows were changed by down';
END
$$;

COMMIT;
