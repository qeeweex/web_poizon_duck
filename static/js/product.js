// Функция для обработки выбора категории доставки
function selectDeliveryCategory(categoryName, cost) {
    // Обновляем отображаемую стоимость доставки
    document.getElementById('delivery-cost').textContent = cost + ' руб.';
    
    // Получаем текущую цену товара в рублях
    const priceRub = parseFloat(document.getElementById('price-rub').dataset.value || 0);
    
    // Рассчитываем новую итоговую стоимость
    const totalPrice = priceRub + cost;
    
    // Обновляем отображение итоговой стоимости
    document.getElementById('total-price').textContent = totalPrice.toLocaleString() + ' руб.';
    
    // Сохраняем выбранную категорию в data-атрибут для последующей обработки при заказе
    document.getElementById('delivery-category').value = categoryName;
    document.getElementById('delivery-cost-value').value = cost;
    
    // Визуально отмечаем выбранную категорию
    const categoryButtons = document.querySelectorAll('.category-button');
    categoryButtons.forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.name === categoryName) {
            btn.classList.add('active');
        }
    });
}

// Обработка успешного ответа от API при расчете цены
function handleSuccessfulPriceCalculation(data) {
    // Существующий код обработки...
    
    // Добавляем обработку категорий доставки
    if (data.delivery_categories && data.delivery_categories.length > 0) {
        const categoriesContainer = document.getElementById('delivery-categories-container');
        if (categoriesContainer) {
            categoriesContainer.innerHTML = '';
            
            data.delivery_categories.forEach(category => {
                const categoryBtn = document.createElement('div');
                categoryBtn.className = 'category-button';
                categoryBtn.dataset.name = category.name;
                categoryBtn.dataset.cost = category.cost;
                categoryBtn.innerHTML = `
                    <div class="category-icon">
                        ${getCategoryIcon(category.name)}
                    </div>
                    <div class="category-info">
                        <div class="category-name">${category.name}</div>
                        <div class="category-cost">${category.cost} руб.</div>
                    </div>
                `;
                categoryBtn.addEventListener('click', () => {
                    selectDeliveryCategory(category.name, category.cost);
                });
                categoriesContainer.appendChild(categoryBtn);
            });
            
            // Выбираем первую категорию по умолчанию
            if (data.delivery_categories.length > 0) {
                selectDeliveryCategory(
                    data.delivery_categories[0].name, 
                    data.delivery_categories[0].cost
                );
            }
        }
    }
}

// Функция для получения иконки категории
function getCategoryIcon(categoryName) {
    const lowerCaseName = categoryName.toLowerCase();
    if (lowerCaseName.includes('кроссовки')) {
        return '<i class="fas fa-shoe-prints"></i>';
    } else if (lowerCaseName.includes('одежда')) {
        return '<i class="fas fa-tshirt"></i>';
    } else if (lowerCaseName.includes('аксессуары')) {
        return '<i class="fas fa-glasses"></i>';
    } else {
        return '<i class="fas fa-box"></i>';
    }
}
