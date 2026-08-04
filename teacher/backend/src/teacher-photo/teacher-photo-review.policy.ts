export const TEACHER_PHOTO_CRITERIA_VERSION =
  'lesson-preparation-camera-view-2026-08-v7-background-veto';
export const TEACHER_PHOTO_MIN_CONFIDENCE = 0.85;
export const TEACHER_PHOTO_CAMERA_ANGLE_MIN_CONFIDENCE = 0.9;
export const TEACHER_PHOTO_MIN_WIDTH = 640;
export const TEACHER_PHOTO_MIN_HEIGHT = 360;

export function teacherPhotoMinimumConfidence(criterionKey: string): number {
  return criterionKey === 'camera_angle'
    ? TEACHER_PHOTO_CAMERA_ANGLE_MIN_CONFIDENCE
    : TEACHER_PHOTO_MIN_CONFIDENCE;
}

export const TEACHER_PHOTO_CRITERIA = [
  {
    id: 'camera_angle',
    title: '摄像头角度',
    suggestion:
      '正对摄像头，让双眼、鼻部和嘴部正面清楚可见；将脸部贴合椭圆辅助线、肩部贴合下方轮廓，并让镜头与视线平齐。',
  },
  {
    id: 'lighting',
    title: '光线',
    suggestion:
      '让面部两侧五官都能清楚辨认，使用正面均匀光线；避免明显过暗、重阴影、过曝、强逆光或滤镜遮盖。',
  },
  {
    id: 'background',
    title: '背景',
    suggestion:
      '请换到干净、整洁、安静、稳定且适合正式线上授课的区域，确保背景中没有其他人员、明显杂物、干扰物或敏感个人信息。',
  },
  {
    id: 'dressing',
    title: '着装',
    suggestion:
      '穿着整洁、专业且适合正式授课，上衣与肩颈区域需清楚可见；睡衣、居家服、无袖、明显过于休闲或干扰性强的服饰不通过。',
  },
] as const;

export const TEACHER_PHOTO_CRITERIA_KEYS = TEACHER_PHOTO_CRITERIA.map(
  (criterion) => criterion.id,
);

export const TEACHER_PHOTO_SYSTEM_PROMPT = `你是新师训练营的摄像头画面审核器。只根据输入的当前摄像头画面进行判断，必须严格输出 JSON，不得输出 Markdown。不要判断或推断年龄、种族、健康、宗教等敏感属性。

先执行硬性前置检查：画面必须可读取、为清晰的 16:9 横向照片、只出现一位老师；老师的面部主要轮廓从额头到下巴完整可见，没有被手、手机、口罩、墨镜或画面边缘明显遮挡，且不存在严重模糊、马赛克或压缩失真。无法读取时 decision=ERROR；其余任一前置条件不满足时，将 camera_angle 或对应项目判为 FAIL，并 decision=RETRY。

逐项严格检查以下四项，不能因为画面中有人就默认通过：

输入图像上有后端临时叠加的白色虚线辅助轮廓，它与老师拍照时看到的辅助线完全一致。虚线不是实际背景、遮挡物或着装内容：只用它判断 camera_angle，检查 lighting、background、dressing 时必须忽略虚线。

1 camera_angle：必须正脸面对摄像头，双眼、鼻部和嘴部均从正面清楚可见，面部左右透视基本对称；明显侧脸、转头、仰头、低头或头部向左/向右侧倾一律 FAIL。两眼连线应接近水平、脸部中轴应接近竖直，偏斜约 10° 或以上时 FAIL。构图必须直接对照图中的虚线：椭圆应大致覆盖从额头到下巴的正脸区域，脸部中心应与椭圆中心重合，不能只让椭圆覆盖半张脸；双肩和胸部以上应大致贴合下方肩部轮廓。等价数值范围为脸部中心横向 45%–55%、纵向 24%–34%，头部可见高度占画面高度 26%–35%，头顶留白占画面高度 8%–16%。过近、过远、脸部偏离椭圆、头部大小不符、裁切头顶或下巴、机位明显过高或过低时 FAIL。允许轻微自然误差，但不得用“基本可见”“身体大致居中”放行脸部未贴合椭圆或头部明显侧倾的画面；

2 lighting：面部两侧五官均清楚可辨，亮度均匀且面部细节没有被滤镜或高光遮盖；明显过暗、重阴影、过曝、强逆光，或者额头、脸颊、鼻部因强眩光大片发白并丢失细节时 FAIL。即使仍能辨认出人脸，也不能放行明显过曝或强眩光；

3 background（硬性项）：背景必须干净、整洁、安静、稳定，并适合正式线上授课。检查整张画面，画面中只能出现当前老师；出现其他人员、明显杂物、干扰授课的物体、敏感个人信息，或虚拟背景破损、穿帮、遮挡时，background 一律 FAIL。只有明确满足全部要求时才能 PASS；

4 dressing：只判断画面中可见的衣着是否整洁、专业并适合正式授课，肩颈与上衣需清楚可见；睡衣、居家服、无袖、明显过于休闲、衣着凌乱或干扰性强的服饰与配饰时 FAIL。

每项同时给出 0 到 1 的 confidence。camera_angle 只有 confidence >= ${TEACHER_PHOTO_CAMERA_ANGLE_MIN_CONFIDENCE} 才能标为 PASS，其余三项只有 confidence >= ${TEACHER_PHOTO_MIN_CONFIDENCE} 才能标为 PASS；证据不足或低于各自阈值时必须标为 UNKNOWN。任何一项 FAIL 或 UNKNOWN，decision 必须是 RETRY；只有四项全部 PASS 才能 decision=PASS。不要检查耳麦、麦克风、扬声器、网络、噪音或设备性能。

输出前必须在内部依次核对 camera_angle 的正脸方向、头部是否侧倾、脸部与椭圆的重合、头部大小和机位高度；任一条件失败，camera_angle 必须为 FAIL，但最终仍只输出 camera_angle 这一项，不得输出内部核对项。还必须单独扫描整张背景，并在 confidenceSummary.backgroundChecks 中输出 otherPeopleVisible、distractingObjectsVisible、clutterVisible、sensitiveInformationVisible、virtualBackgroundDefect 五个布尔值；任一值为 true 时 background 必须为 FAIL。

严格格式：{"decision":"PASS|RETRY|ERROR","teacherReason":"给老师的简短说明","confidenceSummary":{"backgroundChecks":{"otherPeopleVisible":false,"distractingObjectsVisible":false,"clutterVisible":false,"sensitiveInformationVisible":false,"virtualBackgroundDefect":false}},"criteria":[{"criterionKey":"camera_angle","result":"PASS|FAIL|UNKNOWN","teacherMessage":null,"confidence":0.95}]}。criteria 必须且只能包含 camera_angle、lighting、background、dressing，各一次；正脸和辅助线要求全部归入 camera_angle，不得增加第五项或额外检测项。`;

export const TEACHER_PHOTO_USER_TEXT =
  '审核首课准备的当前摄像头画面。严格检查正脸和辅助线构图，并扫描整张背景；背景不干净整洁、出现其他人员、明显杂物或干扰授课的物体时必须判 background=FAIL。只按配置的四项可见标准判断；不要检查耳麦、麦克风或网络。';

const BACKGROUND_VETO_MESSAGES = [
  ['otherPeopleVisible', '背景中出现其他人员，请换到无人入镜的授课区域。'],
  ['distractingObjectsVisible', '背景中有干扰授课的物体，请移开后重新拍照。'],
  ['clutterVisible', '背景中有明显杂物，请整理后重新拍照。'],
  [
    'sensitiveInformationVisible',
    '背景中可能包含敏感个人信息，请遮挡或更换背景。',
  ],
  [
    'virtualBackgroundDefect',
    '虚拟背景存在破损、穿帮或遮挡，请调整后重新拍照。',
  ],
] as const;

export function teacherPhotoBackgroundVetoMessage(
  confidenceSummary: Record<string, unknown>,
): string | null {
  const checks = confidenceSummary.backgroundChecks;
  if (!checks || typeof checks !== 'object' || Array.isArray(checks)) {
    return null;
  }
  for (const [key, message] of BACKGROUND_VETO_MESSAGES) {
    if ((checks as Record<string, unknown>)[key] === true) return message;
  }
  return null;
}
