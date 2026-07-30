import { PasswordHasher } from './password-hasher';

describe('PasswordHasher', () => {
  it('hashes with a random salt and verifies without storing plaintext', async () => {
    const hasher = new PasswordHasher();
    const first = await hasher.hash('a-secure-password');
    const second = await hasher.hash('a-secure-password');

    expect(first).not.toBe(second);
    await expect(hasher.verify('a-secure-password', first)).resolves.toBe(true);
    await expect(hasher.verify('wrong-password', first)).resolves.toBe(false);
    expect(first).not.toContain('a-secure-password');
  });
});
