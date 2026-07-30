import { describe, expect, it } from 'vitest'
import {
  supportTicketCategoryLabel,
  supportTicketContextLabel,
  supportTicketLocationLabel,
  supportTicketWorkflowLabel,
} from './supportTickets'

describe('support ticket labels', () => {
  it('keeps teacher-facing codes readable in both locales', () => {
    expect(supportTicketWorkflowLabel('WAITING_OPERATOR')).toBe('待运营回复')
    expect(supportTicketWorkflowLabel('WAITING_OPERATOR', 'en-US')).toBe('Waiting for operations')
    expect(supportTicketCategoryLabel('SCORE_OR_REVIEW')).toBe('积分与评价')
    expect(supportTicketCategoryLabel('SCORE_OR_REVIEW', 'en-US')).toBe('Scores and reviews')
  })

  it('falls back to the source code instead of inventing a label', () => {
    expect(supportTicketCategoryLabel('NEW_CATEGORY')).toBe('NEW_CATEGORY')
    expect(supportTicketLocationLabel('NEW_PAGE')).toBe('NEW_PAGE')
    expect(supportTicketContextLabel('unknown_key')).toBe('unknown_key')
  })
})
