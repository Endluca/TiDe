BEGIN;

INSERT INTO tide.knowledge_documents (
    id, document_key, version, title, language,
    authority_level, status, content_hash, activated_at
) VALUES (
    '50000000-0000-4000-8000-000000000001',
    'mock-tide-confirmed-rules',
    1,
    '[Mock] TIDE 已确认规则问答',
    'zh',
    'FAQ',
    'ACTIVE',
    'mock-tide-confirmed-rules-v1',
    now()
) ON CONFLICT (document_key, version) DO UPDATE SET
    title = EXCLUDED.title,
    status = 'ACTIVE',
    activated_at = EXCLUDED.activated_at;

INSERT INTO tide.knowledge_chunks (
    id, document_id, chunk_key, position, body, retrieval_metadata
) VALUES
    (
        '51000000-0000-4000-8000-000000000001',
        '50000000-0000-4000-8000-000000000001',
        'video-playback',
        1,
        '[Mock] 视频为什么不能倍速？所有视频类任务都在本系统内观看和完成。视频只允许 1 倍速；第一次完整看完前不能拖动进度条；没有完整看完不计入任务完成进度。',
        '{"answerable":true,"mock":true,"keywords":["视频","倍速","拖动","完整观看"]}'::jsonb
    ),
    (
        '51000000-0000-4000-8000-000000000002',
        '50000000-0000-4000-8000-000000000001',
        'teacher-upload-scope',
        2,
        '[Mock] 教师需要上传什么？教师只在任务流程明确要求提交照片或截图时上传证据。其他视频、图片和文档素材由项目团队开发时配置，前期使用本地素材，后期切换到 OSS 或 CDN。',
        '{"answerable":true,"mock":true,"keywords":["上传","照片","截图","OSS"]}'::jsonb
    ),
    (
        '51000000-0000-4000-8000-000000000003',
        '50000000-0000-4000-8000-000000000001',
        'g01-external-review',
        3,
        '[Mock] G01 怎么完成？Self-intro 和 TESOL 读取世文状态；还需要在本系统完成 61 题并达到通过线、确认 Essay 已完成、提交完成证明。五项全部满足后才能完成 G01。',
        '{"answerable":true,"mock":true,"keywords":["G01","Self-intro","TESOL","审核状态"]}'::jsonb
    )
ON CONFLICT (document_id, chunk_key) DO UPDATE SET
    body = EXCLUDED.body,
    retrieval_metadata = EXCLUDED.retrieval_metadata;

COMMIT;
