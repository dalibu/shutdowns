# TODO List

## High Priority

### Address Management Enhancement
**Status:** Planned  
**Priority:** Medium  
**Estimated effort:** 2-3 hours

**Problem:**
Current "Rename" function only changes alias, which confuses users who want to edit the actual address (city, street, house).

**Solution:**
Replace "Rename" with comprehensive "Edit Address" function:

1. **UI Changes:**
   - Rename button: "Переименовать" → "Редагувати"
   - Show current values when editing

2. **Functionality:**
   ```
   Edit Address Menu:
   ┌─────────────────────────────┐
   │ Редагувати адресу:          │
   │                             │
   │ 📍 Адреса: м. Дніпро,      │
   │   вул. Сонячна набережна, 6│
   │ 🏷️ Назва: "Мій дім"        │
   │                             │
   │ [Змінити назву]             │
   │ [Змінити місто]             │
   │ [Змінити вулицю]            │
   │ [Змінити будинок]           │
   │ [❌ Скасувати]              │
   └─────────────────────────────┘
   ```

3. **Implementation:**
   - New FSM state: `AddressEditState`
   - Options to edit:
     * Alias only (quick)
     * City, Street, or House (creates new address in DB)
   - Validation: check if new address already exists
   - Migration: update user_addresses reference

4. **Database considerations:**
   - If address is unique to user → update addresses table
   - If address shared with others → create new address entry + update user_addresses
   - Preserve subscriptions (migrate to new address_id)

5. **Testing:**
   - Test editing alias
   - Test editing city/street/house
   - Test with existing subscriptions
   - Test with shared vs unique addresses

**Related files:**
- `common/handlers.py` - Edit address handlers
- `common/bot_base.py` - Database update functions
- `common/tests/test_handlers.py` - New tests

**Notes:**
This will significantly improve UX and reduce confusion about "Rename" function.

---

## Low Priority

### Other improvements
- Add /help improvements
- Performance optimizations
- Additional test coverage

---

**Last updated:** 2025-12-13  
**Added by:** AI Assistant during bug fix session
