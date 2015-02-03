from core.models import Volume
from rest_framework import serializers
from .provider_summary_serializer import ProviderSummarySerializer


class VolumeSerializer(serializers.ModelSerializer):
    provider = ProviderSummarySerializer()

    class Meta:
        model = Volume
        fields = ('id', 'size', 'name', 'start_date', 'provider')
