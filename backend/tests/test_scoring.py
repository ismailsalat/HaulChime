import scoring


def base(**overrides):
    data = dict(
        service_type="junk_removal", job_size="single_item", urgency="flexible",
        inventory="One couch", description="Remove one couch from ground floor.",
        photo_count=0, phone_valid=True, phone_verified=False, email=None,
        in_coverage=True, service_accepted=True,
    )
    data.update(overrides)
    return scoring.calculate(**data)


def test_three_fixed_prices():
    standard = base()
    high = base(service_type="local_move", job_size="2br", urgency="this_week",
                inventory="Sofa, beds, tables and 25 boxes", photo_count=3,
                phone_verified=True, email="a@example.com")
    premium = base(service_type="long_distance_move", job_size="3br_plus", urgency="today",
                   inventory="Complete three bedroom home inventory with many boxes and furniture",
                   description="Confirmed move date with full access information and flexible arrival.",
                   photo_count=8, phone_verified=True, email="a@example.com",
                   special_items="Piano")
    assert standard["price"] == 40
    assert high["price"] in (55, 70)
    assert premium["price"] == 70


def test_difficulty_does_not_force_premium():
    result = base(pickup_access="two_plus_flights", destination_access="two_plus_flights",
                  parking_access="difficult", special_items="Very heavy couch")
    assert result["difficulty_score"] > 50
    assert result["price"] == 40
